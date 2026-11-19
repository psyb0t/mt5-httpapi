#!/usr/bin/env python3
"""Compose-managed VM crash watchdog.

dockurr/windows keeps the container "up" while the Windows guest may have
crashed internally (e.g. Event ID 6008 "The previous system shutdown ... was
unexpected"). Docker's healthcheck then reports ``unhealthy``, but because the
container never exits, ``restart: unless-stopped`` never fires and every
terminal API inside that VM stays dead until a human restarts it. This sidecar
is that human, running as a normal Compose service instead of a host cron job.

It polls Docker health through the mounted unix socket and restarts a VM
container ONLY after its Docker health has stayed ``unhealthy`` for a sustained
``FailingStreak`` — the same source of truth ``docker inspect`` exposes. A
healthy or merely-starting VM is never touched, so long-running backtests on a
working VM are never interrupted; the healthcheck goes green the whole time a
terminal is serving, and only a genuinely dead VM stays red.

Restart policy is stateful, not a fixed cooldown:

- A tiny JSON state record per VM lives on a named volume, keyed by STABLE
  COMPOSE IDENTITY (``/state/<project>.<service>.json``): last restart time,
  attempt count, and when the VM was last observed healthy. Never keyed by
  container id: recovery here is a recreate, which REPLACES the container, so
  an id-keyed record is orphaned by the very action that wrote it and the next
  poll would start the replacement at ``attempts = 0`` - every recovery
  silently refunding the attempt budget and resetting backoff.
- After the sustained-unhealthy threshold, the VM is restarted. Attempts gate
  an exponential backoff (default 5m -> 15m -> 1h): a VM that crashes again
  immediately after recovery is not restarted into a loop.
- After ``WATCHDOG_MAX_ATTEMPTS`` consecutive failed recoveries the watchdog
  stops trying and logs loudly, so a genuinely broken VM does not thresh forever.
- Attempts reset only after the VM has stayed healthy for
  ``WATCHDOG_RESET_SECONDS``, so a VM that recovered then crashed again later
  gets a fresh budget instead of inheriting old failures.

Scope is strict: for the VM sweep, only containers in this Compose project
running the ``dockurr/windows`` image are considered, so nginx, the log rotator
and the watchdog itself are never touched.

A SECOND, narrower sweep covers the netns sidecars, because nothing watched
them and the way they fail leaves the VM sweep with nothing to act on. Docker
resolves ``network_mode: service:<vm>`` ONCE, at the sidecar's own start, into
an immutable ``NetworkMode=container:<owner-id>``, and it builds a fresh
namespace whenever the owner starts. So restarting the owner strands the
sidecar - the id is unchanged, the namespace is not - and the VM comes back
perfectly healthy. It happened here: mt5 exited cleanly on 2026-09-07 and
``unless-stopped`` restarted it, and its wickworks sidecar spent two days in a
dead namespace, 502-ing every ``/rates/ta`` call, with a 13,700-long failing
streak nothing was looking at.

The streak is the point. The sidecar's OWN healthcheck saw the fault the whole
time; there was simply no supervisor for it. So this sweep applies the same
rule as the VM sweep - Docker health, past the same streak - and recreates the
sidecar ALONE, which is the documented repair and what an operator does by
hand. It acts only while the owner is a RUNNING, HEALTHY VM of this project:
an unhealthy owner belongs to the VM sweep, which recreates owner and sidecars
together, and an owner that is not running cannot be joined at all, so
recreating a sidecar under it would only leave it stopped.

That makes an orphan-aware sidecar healthcheck a requirement, not a nicety -
see ``scripts/wickworks-healthcheck.py``. Disable the sweep entirely with
``WATCHDOG_WATCH_SIDECARS=0``.

Recovery is a COORDINATED RECREATE, not a restart. A sidecar sharing the VM's
network namespace (``network_mode: service:<vm>``, i.e. wickworks) resolves
that binding once, at its own start, into an immutable
``NetworkMode=container:<owner-id>``. Restarting the owner keeps its id, but
Docker tears the netns down on stop and builds a fresh one on start, so the
sidecar is left holding a dead namespace. This module previously claimed the
opposite; ``tests/integration/test_wickworks_lifecycle.py`` disproves it,
asserting that BOTH "recreate owner alone" and "restart owner alone" strand
the sidecar, and that only recreating the owner together with its sidecars
repairs the binding.

So the watchdog does not restart through the Docker API. It shells out to
``scripts/recreate-vm.sh`` — the same helper an operator runs by hand, and the
one that lifecycle test covers — which discovers each VM's sidecars from the
generated Compose file and recreates them together.

That is why this container needs the compose project mounted at the SAME
absolute path the host uses: Compose resolves relative bind mounts
client-side, so ``./scripts/x`` in the compose file has to land on the host's
``<project>/scripts/x``, not on some path inside this container. ``run.sh``
exports ``MT5_PROJECT_DIR`` for that and persists it to ``.env``. Without it
the watchdog refuses to act and says so, rather than falling back to a
restart that looks like recovery and is not.

The helper runs ``docker compose`` itself, and compose interpolates
``${MT5_PROJECT_DIR:?}`` in the compose file on EVERY command — so the
watchdog hands that variable to the helper explicitly (``recreate_env``).
Compose resolved it on the host when it created this container but never
passed it in, and without that line every real recovery failed at
interpolation before it could stop anything.

Runs forever, restarting itself if it crashes: keepalive is the Compose
``restart: unless-stopped`` on this service, not a supervisor inside the loop.
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

# ── Configuration (env-overridable, defaults match the sidecar in compose) ──

# Misconfiguration is collected rather than raised, so importing this module
# never explodes and validate_config() can report EVERY problem at once. An
# operator fixing one variable at a time from successive tracebacks is a worse
# afternoon than one message listing all of them.
CONFIG_ERRORS: list[str] = []


def _env_int(name: str, default: str, minimum: int) -> int:
    """Integer from the environment, or the default plus a recorded error.

    Falling back to the default keeps the module importable; validate_config()
    is what refuses to run. Blank is rejected explicitly: an unset variable and
    one set to "" mean different things to the person who wrote the compose
    file, and only the second is a mistake worth naming.
    """
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    text = raw.strip()
    if not text:
        CONFIG_ERRORS.append(f"{name} is set but empty; expected an integer >= {minimum}")
        return int(default)
    try:
        value = int(text)
    except ValueError:
        CONFIG_ERRORS.append(f"{name}={raw!r} is not an integer")
        return int(default)
    if value < minimum:
        CONFIG_ERRORS.append(f"{name}={value} is below the minimum of {minimum}")
        return int(default)
    return value


def _env_int_list(name: str, default: str, minimum: int) -> list[int]:
    """Comma-separated positive integers, never empty.

    The empty case is the one that mattered: WATCHDOG_BACKOFF_ATTEMPTS= parsed
    to [], which survived startup and then raised IndexError inside decide()
    on the first unhealthy pass after a recorded restart - a crash loop in the
    thing that exists to recover from crashes.
    """
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        CONFIG_ERRORS.append(
            f"{name}={raw!r} parsed to an empty list; expected at least one integer >= {minimum}"
        )
        return [int(p) for p in default.split(",")]
    values: list[int] = []
    for part in parts:
        try:
            value = int(part)
        except ValueError:
            CONFIG_ERRORS.append(f"{name} entry {part!r} is not an integer")
            continue
        if value < minimum:
            CONFIG_ERRORS.append(f"{name} entry {value} is below the minimum of {minimum}")
            continue
        values.append(value)
    if not values:
        return [int(p) for p in default.split(",")]
    return values


def _env_bool(name: str, default: str) -> bool:
    """A yes/no switch, tolerant of how people actually write them.

    Anything unrecognised is a recorded error and falls back to the default
    rather than being read as false: silently disabling a recovery path
    because someone wrote ``WATCHDOG_WATCH_SIDECARS=no thanks`` is the failure
    mode this whole file is written against.
    """
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    CONFIG_ERRORS.append(f"{name}={raw!r} is not a boolean (1/0, true/false, yes/no, on/off)")
    return default.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str, *, allow_empty: bool) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if not raw.strip() and not allow_empty:
        CONFIG_ERRORS.append(f"{name} is set but empty; expected a value")
        return default
    return raw


DOCKER_SOCKET = _env_str("WATCHDOG_DOCKER_SOCKET", "/var/run/docker.sock", allow_empty=False)

# The coordinated recreate helper, and the compose project it must act on.
# PROJECT_DIR is the HOST path: compose resolves the relative bind mounts in
# docker-compose.yml against it, so a container-local path would rewrite every
# mount to somewhere that does not exist on the host. Empty means "not wired
# up" and is reported by validate_config() rather than guessed at.
RECREATE_SCRIPT = _env_str(
    "WATCHDOG_RECREATE_SCRIPT", "/project/scripts/recreate-vm.sh", allow_empty=False
)
PROJECT_DIR = _env_str("WATCHDOG_PROJECT_DIR", "", allow_empty=True)
RECREATE_TIMEOUT_SECONDS = _env_int("WATCHDOG_RECREATE_TIMEOUT", "300", minimum=30)
STATE_DIR = _env_str("WATCHDOG_STATE_DIR", "/state", allow_empty=False)
INTERVAL_SECONDS = _env_int("WATCHDOG_INTERVAL_SECONDS", "30", minimum=1)

# Restart only after this many consecutive failed healthchecks.
MIN_FAILING_STREAK = _env_int("WATCHDOG_MIN_FAILING_STREAK", "10", minimum=1)

# Only containers running exactly this image REPOSITORY (any tag or digest)
# are considered VM containers - see _image_matches. Empty is refused rather
# than treated as "no filter": a blank value would make every container in the
# project a restart candidate - including any database sharing it.
IMAGE_FILTER = _env_str("WATCHDOG_IMAGE_FILTER", "dockurr/windows", allow_empty=False)

# Also watch the sidecars that share a VM's network namespace
# (``network_mode: service:<vm>``). Restarting the owner strands them - same
# container id, fresh namespace - while the VM itself comes back healthy, so
# the VM sweep has nothing to act on. Their own healthchecks see it; nothing
# was watching those. On this farm that cost two days of dead TA calls on
# 2026-09-07. Set to 0 to restore the VM-only scope.
WATCH_SIDECARS = _env_bool("WATCHDOG_WATCH_SIDECARS", "1")

# Exponential backoff per attempt: first restart after 5m, then 15m, then 1h.
BACKOFF_ATTEMPTS = _env_int_list("WATCHDOG_BACKOFF_ATTEMPTS", "300,900,3600", minimum=1)

# Give up (and log loudly) after this many consecutive failed recoveries.
MAX_ATTEMPTS = _env_int("WATCHDOG_MAX_ATTEMPTS", "3", minimum=1)

# A VM must stay healthy this long before its attempt budget resets. Zero is
# refused: the budget would reset on the first healthy poll after a restart,
# which silently makes MAX_ATTEMPTS unreachable and the give-up path dead code.
RESET_SECONDS = _env_int("WATCHDOG_RESET_SECONDS", "1800", minimum=1)

# A container that has never been healthy is still on its first boot: the
# first `make up` installs Windows, Python and MetaTrader, and the API that the
# healthcheck probes does not exist until that finishes, which takes far
# longer than start_period plus the failing streak. Recreating it then only
# restarts the install. So a service is left alone until it has been healthy
# once, or until this many seconds have passed since the watchdog first saw it
# without ever seeing it healthy (an install that is truly stuck still gets
# recovered).
FIRST_BOOT_GRACE_SECONDS = _env_int("WATCHDOG_FIRST_BOOT_GRACE_SECONDS", "10800", minimum=1)

# Compose project to scope to. Empty = auto-discover from this container's own
# labels (the watchdog runs as a service in the same project).
COMPOSE_PROJECT = os.environ.get("WATCHDOG_COMPOSE_PROJECT", "")

# This container's own ID; Docker sets the container hostname to its SHORT id.
SELF_ID = os.environ.get("WATCHDOG_SELF_ID", socket.gethostname())
# Resolved to the FULL id at startup (main -> _inspect_self) so self-exclusion
# works by equality even when the hostname is not the container id - a
# `hostname:` on the service, or WATCHDOG_SELF_ID set to a name.
SELF_FULL_ID = ""
_HEX_ID = re.compile(r"[0-9a-f]{12,64}")


def log(message: str, *args) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = message % args if args else message
    print(f"[{stamp}] [vm-watchdog] {text}")


# ── Minimal Docker Engine API client over the unix socket (stdlib only) ──────


class _UnixSocketConnection(http.client.HTTPConnection):
    """HTTPConnection that talks to the Docker Engine over a unix socket."""

    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        self.sock = sock


class DockerClient:
    """A tiny read-only Docker API client.

    Only the endpoints the watchdog needs are implemented: list containers and
    inspect one. Recovery deliberately does NOT go through this client — see
    recreate_vm() and the module docstring. Anything else the daemon replies
    with is surfaced as a RuntimeError carrying the status + body.
    """

    def __init__(self, socket_path: str = DOCKER_SOCKET):
        self._socket_path = socket_path

    def _request(self, method: str, path: str, body: str | None = None) -> bytes:
        conn = _UnixSocketConnection(self._socket_path)
        try:
            conn.request(method, path, body=body)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
        finally:
            conn.close()
        if not (200 <= status < 300):
            raise RuntimeError(
                f"docker {method} {path}: HTTP {status}: {data[:300]!r}"
            )
        return data

    def list_containers(self) -> list[dict]:
        """Running containers (Docker's default ``/containers/json``)."""
        data = self._request("GET", "/containers/json")
        return json.loads(data) if data else []

    def inspect(self, container_id: str) -> dict:
        data = self._request("GET", f"/containers/{container_id}/json")
        return json.loads(data)



def recreate_env(project: str) -> dict[str, str]:
    """The exact environment the recreate helper runs with.

    Two values are set explicitly, because the helper runs ``docker compose``
    and compose needs both while this container was handed neither:

    - ``COMPOSE_PROJECT_NAME``: compose otherwise derives the project from the
      directory name, and a mismatch does not fail - it quietly creates a
      SECOND set of containers alongside the running ones.
    - ``MT5_PROJECT_DIR``: the compose file interpolates ``${MT5_PROJECT_DIR:?}``
      on EVERY compose command, not only the ``up`` that started the stack.
      Compose resolved it on the host when it created this container, but the
      container's own environment carries ``WATCHDOG_PROJECT_DIR`` instead, so
      the helper's compose call failed at interpolation - "required variable
      MT5_PROJECT_DIR is missing a value" - before it could stop or recreate
      anything. Every real (non-dry-run) recovery was dead on arrival.
      ``WATCHDOG_PROJECT_DIR`` IS that host path, so it is the source here.

    Factored out of recreate_vm() so a test can run the real helper under
    precisely this environment, with nothing inherited from the host shell.
    """
    env = dict(os.environ)
    env["COMPOSE_PROJECT_NAME"] = project
    env["MT5_PROJECT_DIR"] = PROJECT_DIR
    return env


def recreate_vm(service: str, project: str) -> None:
    """Recreate one VM together with every sidecar sharing its netns.

    Delegates to scripts/recreate-vm.sh instead of reimplementing it: that
    script is what operators run, what the lifecycle regression test exercises,
    and the only place that knows how to find a VM's sidecars in the generated
    compose file. Reimplementing the discovery here would give the fleet two
    recovery paths that could drift apart. See recreate_env() for the two
    variables the helper is handed explicitly.
    """
    if not PROJECT_DIR:
        raise RuntimeError(
            "WATCHDOG_PROJECT_DIR is unset, so the compose project cannot be "
            "recreated. Start the stack through run.sh (it exports "
            "MT5_PROJECT_DIR and writes it to .env), or set it to the host "
            "path of the project."
        )
    result = subprocess.run(
        [RECREATE_SCRIPT, service],
        cwd=PROJECT_DIR,
        env=recreate_env(project),
        capture_output=True,
        text=True,
        timeout=RECREATE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(
            f"{RECREATE_SCRIPT} {service} exited {result.returncode}: {detail}"
        )


# ── Pure policy: state + health in, action out ───────────────────────────────


def state_key(project: str, service: str) -> str:
    """Stable identity for one VM's state record.

    Compose project + service survives a recreate; the container id does not.
    Sanitized defensively so a hostile-looking label cannot become a path -
    both values come from compose labels, but this file name is the only place
    they touch the filesystem.
    """
    raw = f"{project}.{service}"
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)


def load_state(key: str, state_dir: str | None = None) -> dict:
    """The per-VM state record, or a fresh one when absent/corrupt."""
    path = Path(state_dir if state_dir is not None else STATE_DIR) / f"{key}.json"
    if not path.exists():
        return {"last_restart": 0, "attempts": 0, "healthy_since": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"last_restart": 0, "attempts": 0, "healthy_since": 0}


def save_state(key: str, state: dict, state_dir: str | None = None) -> None:
    path = Path(state_dir if state_dir is not None else STATE_DIR) / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def decide(state: dict, health_status: str, failing_streak: int, now: int) -> tuple[str, str]:
    """One container's next action given its state and Docker health.

    Mutates ``state`` (attempts / timestamps) and returns ``(action, reason)``
    where action is one of ``wait``, ``restart`` or ``give_up``.
    """
    # healthy_since means CONTINUOUSLY healthy since. Any other observation -
    # starting, unhealthy, none - breaks that continuity and the clock starts
    # over on the next healthy poll. It used to survive a `starting` (and an
    # unhealthy poll below the streak threshold), so a VM that was healthy at
    # t0, went through a restart, and came back healthy at t0+RESET had its
    # attempt budget refunded on that first healthy poll rather than after
    # RESET_SECONDS of proven stability.
    if health_status != "healthy":
        state["healthy_since"] = 0
    else:
        state["ever_healthy"] = True
    if not state.get("ever_healthy"):
        state.setdefault("first_seen", now)

    if health_status != "unhealthy":
        # Healthy or starting: never touch. Track when the current healthy run
        # began so a recovery that holds long enough resets the attempt budget.
        if health_status == "healthy":
            if not state.get("healthy_since"):
                state["healthy_since"] = now
            if now - state["healthy_since"] >= RESET_SECONDS:
                state["attempts"] = 0
                state["last_restart"] = 0
                return "wait", "healthy long enough - attempt budget reset"
        return "wait", f"health={health_status}"

    # Unhealthy from here on.
    if failing_streak < MIN_FAILING_STREAK:
        return "wait", f"unhealthy streak {failing_streak} < {MIN_FAILING_STREAK}"

    if not state.get("ever_healthy"):
        waited = now - state["first_seen"]
        if waited < FIRST_BOOT_GRACE_SECONDS:
            grace = FIRST_BOOT_GRACE_SECONDS
            return "wait", f"never healthy yet, first boot or install ({waited}s of {grace}s grace)"

    attempts = state.get("attempts", 0)
    if attempts >= MAX_ATTEMPTS:
        return "give_up", f"attempts {attempts} >= MAX_ATTEMPTS {MAX_ATTEMPTS}"

    last_restart = state.get("last_restart", 0)
    # Delay before the NEXT restart, indexed by how many restarts already
    # happened: after the 1st restart wait BACKOFF[0], after the 2nd wait
    # BACKOFF[1], etc. First restart has no prior wait.
    backoff_index = max(0, min(attempts - 1, len(BACKOFF_ATTEMPTS) - 1))
    delay = BACKOFF_ATTEMPTS[backoff_index] if attempts else 0
    if last_restart and (now - last_restart) < delay:
        return "wait", f"backoff {now - last_restart}s < {delay}s (attempt {attempts + 1})"

    state["attempts"] = attempts + 1
    state["last_restart"] = now
    state["healthy_since"] = 0
    reason = f"unhealthy streak {failing_streak} >= {MIN_FAILING_STREAK} (attempt {attempts + 1})"
    return "restart", reason


# ── Docker-facing logic ──────────────────────────────────────────────────────


def _inspect_self(client: DockerClient) -> dict:
    """This container's own inspect record, or {} when it cannot be read."""
    try:
        return client.inspect(SELF_ID)
    except (RuntimeError, OSError):
        return {}


def _compose_project(client: DockerClient) -> str | None:
    """This project's name, from our own container's compose labels."""
    info = _inspect_self(client)
    return (info.get("Config", {}).get("Labels", {}) or {}).get("com.docker.compose.project")


def _image_matches(image: str, repo: str) -> bool:
    """Exact image REPOSITORY match, any tag or digest.

    ``dockurr/windows`` matches ``dockurr/windows``, ``dockurr/windows:5.14``
    and ``dockurr/windows@sha256:...`` - and not ``dockurr/windows-not-the-vm``.
    A prefix test did match that last one, which for a container holding the
    Docker socket meant its restart scope was "anything whose image name
    happens to start the same way".
    """
    return image == repo or image.startswith(repo + ":") or image.startswith(repo + "@")


def _is_self(container_id: str) -> bool:
    """Whether this id is the watchdog's own container.

    By equality against the full id resolved at startup. Before that (or if
    the self-inspect failed) fall back to the short-id prefix Docker gives the
    hostname - but only when SELF_ID actually looks like one, so a service
    `hostname: watchdog` never prefix-matches unrelated containers, and a real
    name never quietly matches nothing while the docs promise otherwise.
    """
    if SELF_FULL_ID:
        return container_id == SELF_FULL_ID
    if SELF_ID and _HEX_ID.fullmatch(SELF_ID):
        return container_id == SELF_ID or container_id.startswith(SELF_ID)
    return False


def _scoped_containers(client: DockerClient, project: str) -> list[dict]:
    """Running containers in this project running the VM image - never itself."""
    out = []
    for c in client.list_containers():
        # Unconditional and first: a valid operator override such as
        # WATCHDOG_IMAGE_FILTER=python would otherwise select the watchdog, and
        # a watchdog that recreates itself mid-sweep is not a recovery path.
        if _is_self(c.get("Id") or ""):
            continue
        labels = c.get("Labels", {}) or {}
        if project and labels.get("com.docker.compose.project") != project:
            continue
        if not _image_matches(c.get("Image") or "", IMAGE_FILTER):
            continue
        out.append(c)
    return out


def _netns_sidecars(client: DockerClient, project: str) -> list[tuple[dict, str]]:
    """(container, owner-id) for every project container bound to another's netns.

    ``network_mode: service:<vm>`` shows up on the running container as
    ``HostConfig.NetworkMode = "container:<owner-id>"`` - the resolved id, not
    the service name, because Docker resolves it once at start and never
    revisits it. That resolved id is exactly what makes the binding breakable,
    and it is what this returns alongside the container.

    Read from the container list rather than a per-container inspect: the list
    already carries HostConfig, and one API call per sweep beats one per
    container on a host with two dozen of them.
    """
    out: list[tuple[dict, str]] = []
    for c in client.list_containers():
        if _is_self(c.get("Id") or ""):
            continue
        labels = c.get("Labels", {}) or {}
        if project and labels.get("com.docker.compose.project") != project:
            continue
        mode = ((c.get("HostConfig") or {}) or {}).get("NetworkMode") or ""
        if not mode.startswith("container:"):
            continue
        # An empty token after `container:` needs no guard here: it matches no
        # VM in _owner_container, which is where every owner reference is
        # resolved, so there is one place that decides and not two.
        out.append((c, mode.split(":", 1)[1]))
    return out


def _owner_container(token: str, vms: list[dict]) -> dict | None:
    """The VM container a sidecar's ``container:<token>`` names, if any.

    ``network_mode: service:<vm>`` always resolves to a full id (verified
    against the daemon), but ``container:<name>`` and ``container:<short-id>``
    are both legal to write by hand and are NOT normalised anywhere. Matching
    only full ids would leave such a sidecar permanently unrecognised, and this
    file's rule is that an unrecognised container is left alone - so it would
    silently go unwatched while the docs promised otherwise.
    """
    for vm in vms:
        cid = vm.get("Id") or ""
        if not cid:
            continue
        if cid == token or (len(token) >= 12 and cid.startswith(token)):
            return vm
        if any(name.lstrip("/") == token for name in (vm.get("Names") or [])):
            return vm
    return None


def _health_of(client: DockerClient, container_id: str) -> tuple[str, int]:
    """(State.Health.Status, State.Health.FailingStreak) or ('none', 0)."""
    info = client.inspect(container_id)
    health = info.get("State", {}).get("Health", {}) or {}
    status = health.get("Status", "none")
    streak = health.get("FailingStreak", 0)
    return status, int(streak or 0)


def sweep_once(
    client: DockerClient,
    project: str,
    dry_run: bool = False,
    now: int | None = None,
) -> int:
    """One pass over the project's VMs, then over their netns sidecars.

    Returns the recovery count across both.
    """
    now = now if now is not None else int(time.time())
    restarted = 0
    # What the VM pass saw, for the sidecar pass that follows: a sidecar's fate
    # depends on its owner's, and re-deriving it there would be a second,
    # divergent opinion about the same containers in the same tick.
    vm_health: dict[str, str] = {}
    vms = _scoped_containers(client, project)
    for c in vms:
        cid = c["Id"]
        name = (c.get("Names") or ["?"])[0]
        try:
            status, streak = _health_of(client, cid)
        except (RuntimeError, OSError) as exc:
            log(f"{name}: cannot inspect health ({exc}); skipping")
            continue
        # Resolve the compose service BEFORE touching state: it is both what a
        # recreate names and the stable half of the state key. A recreate
        # replaces the container, so state keyed by container id was orphaned
        # by every successful recovery - the replacement arrived with a fresh
        # id, loaded a fresh record, and the attempt cap and backoff never
        # carried across the one boundary they exist to police.
        vm_health[cid] = status
        service = (c.get("Labels") or {}).get("com.docker.compose.service")
        if not service:
            log(f"{name}: no compose service label; cannot recreate, skipping")
            continue
        state = load_state(state_key(project, service))
        # decide() MUTATES the state it is handed - it increments attempts and
        # stamps last_restart. Under --dry-run that must not reach disk: a dry
        # pass would consume the real backoff and attempt budget without
        # restarting anything, so enough dry passes leave the VM at GIVING UP
        # the moment dry-run is switched off. Evaluate against a copy instead,
        # and persist nothing.
        working = copy.deepcopy(state) if dry_run else state
        action, reason = decide(working, status, streak, now)
        if not dry_run:
            save_state(state_key(project, service), working)
        if action == "restart":
            # Recreate by COMPOSE SERVICE, not container id: the helper has to
            # name the VM and its sidecars as compose services to recreate them
            # together, and a container id means nothing to compose.
            if dry_run:
                log(f"DRY-RUN: would recreate {service} (+ its sidecars) - {reason}")
            else:
                try:
                    recreate_vm(service, project)
                    log(f"recreated {service} and its sidecars - {reason}")
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    log(f"{name}: recreate failed ({exc}); state kept for backoff")
            restarted += 1
        elif action == "give_up":
            log(f"GIVING UP on {name} after {working.get('attempts', 0)} attempts - {reason}")

    if WATCH_SIDECARS:
        restarted += sweep_sidecars_once(
            client, project, vms, vm_health, dry_run=dry_run, now=now
        )
    return restarted


def sweep_sidecars_once(
    client: DockerClient,
    project: str,
    vms: list[dict],
    vm_health: dict[str, str],
    *,
    dry_run: bool = False,
    now: int | None = None,
) -> int:
    """One pass over the sidecars sharing a VM's netns. Returns recovery count.

    Same rule as the VM sweep - Docker health, past the streak - applied to a
    container class the VM sweep is not allowed to touch. What changes is only
    WHOSE health decides and WHAT gets recreated:

    - Owner is a running project VM and healthy: the sidecar's own health
      decides, and a recovery recreates the SIDECAR ALONE.
    - Owner is a running project VM and not healthy: left alone. Recreating
      owner and sidecars together is the only thing that repairs the binding,
      and that is the VM sweep's job; acting here would race it and rebind to
      a namespace about to be replaced.
    - Owner is not a running project VM: left alone, and this is load-bearing
      rather than a default. A stopped owner is INDISTINGUISHABLE from a
      destroyed one over ``/containers/json``, which lists running containers
      only. Recreating a sidecar under a stopped owner cannot work - the
      helper stops it first, then ``up --no-deps`` cannot join a namespace
      that is not there - so the sidecar would be left STOPPED, invisible to
      both sweeps, and never retried. ``docker compose stop mt5`` for
      maintenance must not cost the sidecar.

    This means a netns sidecar has to have a healthcheck that can SEE the
    orphaning. A check that only probes loopback stays green inside a dead
    namespace and nothing here will ever fire. ``scripts/wickworks-healthcheck.py``
    is the worked example: it probes the owner's gateway services, which
    disappear the moment the namespace does.
    """
    now = now if now is not None else int(time.time())
    recovered = 0
    for c, owner_token in _netns_sidecars(client, project):
        name = (c.get("Names") or ["?"])[0]
        service = (c.get("Labels") or {}).get("com.docker.compose.service")
        if not service:
            log(f"{name}: no compose service label; cannot recreate, skipping")
            continue

        owner = _owner_container(owner_token, vms)
        if owner is None:
            # Not one of this project's running VMs: somebody else's netns, a
            # sidecar chained to a sidecar, or an owner that is stopped. None
            # of those is this sweep's to act on.
            continue
        owner_id = owner.get("Id") or ""
        # Only a healthy owner. An unhealthy one belongs to the VM sweep,
        # which recreates it together with its sidecars - the only operation
        # that repairs the binding - and acting here would race that. This
        # also covers the owner the VM sweep recreated moments ago in this
        # same pass: a recreate only ever follows an UNHEALTHY observation, so
        # such an owner can never be `healthy` here.
        if vm_health.get(owner_id) != "healthy":
            continue

        try:
            status, streak = _health_of(client, c["Id"])
        except (RuntimeError, OSError) as exc:
            log(f"{name}: cannot inspect health ({exc}); skipping")
            continue

        state = load_state(state_key(project, service))
        working = copy.deepcopy(state) if dry_run else state
        action, reason = decide(working, status, streak, now)
        if not dry_run:
            save_state(state_key(project, service), working)
        if action == "restart":
            # The sidecar ALONE. recreate-vm.sh discovers the netns sidecars of
            # the service it is given, and nothing declares
            # `network_mode: service:<sidecar>`, so naming one here recreates
            # exactly one container - the documented repair, which does not
            # disturb the VM or the terminals running inside it.
            if dry_run:
                log(f"DRY-RUN: would recreate sidecar {service} - {reason}")
            else:
                try:
                    recreate_vm(service, project)
                    log(f"recreated sidecar {service} - {reason}")
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    log(f"{name}: sidecar recreate failed ({exc}); state kept for backoff")
            recovered += 1
        elif action == "give_up":
            log(
                f"GIVING UP on sidecar {name} after {working.get('attempts', 0)} "
                f"attempts - {reason}"
            )
    return recovered


def validate_config() -> list[str]:
    """Every configuration problem found at import, as human-readable lines.

    Separate from main() so a test can assert on the messages without running
    the daemon, and so an operator can see all of them in one go.

    The recovery wiring is checked here rather than at the moment a VM dies:
    finding out that the watchdog cannot act only once something has already
    crashed is the worst possible time to learn it.
    """
    errors = list(CONFIG_ERRORS)
    if not PROJECT_DIR:
        errors.append(
            "WATCHDOG_PROJECT_DIR is empty - recovery needs the compose project's "
            "HOST path (run.sh exports MT5_PROJECT_DIR and writes it to .env). "
            "Without it a crashed VM cannot be recreated."
        )
    elif not os.path.isdir(PROJECT_DIR):
        errors.append(
            f"WATCHDOG_PROJECT_DIR={PROJECT_DIR!r} is not a directory in this "
            "container - mount the project through at the same absolute path the "
            "host uses, or compose will rewrite every relative bind mount."
        )
    if not os.path.exists(RECREATE_SCRIPT):
        errors.append(
            f"WATCHDOG_RECREATE_SCRIPT={RECREATE_SCRIPT!r} not found - the "
            "coordinated recreate helper must be mounted into this container."
        )
    return errors


def main() -> int:
    problems = validate_config()
    if problems:
        # Refuse to start rather than run on defaults. This daemon restarts
        # containers; quietly substituting values an operator did not choose is
        # the wrong failure mode for something holding the Docker socket.
        log(f"invalid configuration ({len(problems)} problem(s)); refusing to start")
        for problem in problems:
            log(f"  {problem}")
        return 2

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        log("dry-run mode: will report decisions without restarting")
    client = DockerClient(DOCKER_SOCKET)

    # Resolve our own FULL container id once, so self-exclusion is an equality
    # test rather than a guess from the hostname.
    global SELF_FULL_ID
    self_info = _inspect_self(client)
    SELF_FULL_ID = self_info.get("Id") or ""
    if SELF_FULL_ID:
        log(f"own container id resolved: {SELF_FULL_ID[:12]}")
    else:
        log(
            f"cannot inspect own container '{SELF_ID}'; "
            "self-exclusion falls back to the short-id prefix"
        )

    project = COMPOSE_PROJECT or _compose_project(client) or ""
    if not project:
        log("cannot determine compose project (no WATCHDOG_COMPOSE_PROJECT and "
            "self-inspect found no compose labels); refusing to run un-scoped")
        return 1
    log(f"scoped to compose project '{project}' (image filter '{IMAGE_FILTER}')")

    while True:
        try:
            sweep_once(client, project, dry_run=dry_run)
        except (RuntimeError, OSError) as exc:
            log(f"sweep failed: {exc}")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
