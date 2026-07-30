"""Integration test: real nginx, real generated config, fake upstreams.

tests/test_config_generation.py asserts the SHAPE of the generated nginx config
with regexes. Those encode what we BELIEVE is safe and never ask nginx, so a
config that is structurally what we expected but that nginx rejects still
passes. This module asks nginx.

The case that matters is partial outage. A literal `proxy_pass http://host:port`
resolves at config-PARSE time, so ONE absent VM stops nginx starting at all and
takes every healthy terminal down with it. Only a real nginx can prove the
resolver+variable form fixes that.

These spawn sibling containers through the host docker socket, so they run on
the host (`make test-integration`) rather than inside the offline test image.

"""

import base64
import importlib.util
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

NGINX_IMAGE = "nginx:1.27-alpine"
PYTHON_IMAGE = "python:3.12-alpine"

LIVE_ALIAS = "vm-live"
DOWN_ALIAS = "vm-down"
LIVE_PORT = 5001
DOWN_PORT = 5002
NGINX_PORT = 80

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_BAD_GATEWAY = 502
REQUEST_TIMEOUT_SECONDS = 8
STARTUP_TIMEOUT_SECONDS = 30

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG_ENV = "TEST_NGINX_CONFIG_B64"
NGINX_CONFIG_PATH = "/etc/nginx/nginx.conf"
NGINX_BOOT_COMMAND = (
    f'printf "%s" "${{{NGINX_CONFIG_ENV}}}" | base64 -d >{NGINX_CONFIG_PATH} '
    '&& exec nginx -g "daemon off;"'
)


def _load_config_helper():
    module_path = REPO_ROOT / "scripts" / "config_helper.py"
    spec = importlib.util.spec_from_file_location("config_helper_integration", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _render_nginx_conf(tmp_path):
    """Renders through the REAL config_helper — the point is to test what ships,
    not a hand-written approximation of its output.

    Two terminals on two VMs, one of which never starts. That is the exact
    topology a literal upstream cannot survive.
    """
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "api_token": "",
                "terminals": [
                    {"broker": "acme", "account": "live", "port": LIVE_PORT, "vm": "livevm"},
                    {"broker": "acme", "account": "down", "port": DOWN_PORT, "vm": "downvm"},
                ],
            }
        ),
        encoding="utf-8",
    )

    vms_path = tmp_path / "vms.yaml"
    vms_path.write_text(
        yaml.safe_dump(
            {
                "vms": [
                    {"name": "livevm", "service": LIVE_ALIAS, "container_name": LIVE_ALIAS},
                    {"name": "downvm", "service": DOWN_ALIAS, "container_name": DOWN_ALIAS},
                ]
            }
        ),
        encoding="utf-8",
    )

    helper = _load_config_helper()
    helper.CONFIG_PATH = str(config_path)
    helper.VMS_PATH = str(vms_path)
    helper._VMS_EXAMPLE_PATH = str(vms_path)

    outpath = tmp_path / "nginx.conf"
    import sys

    sys.argv = ["config_helper.py", "nginx_conf", str(outpath)]
    helper.main()

    conf = outpath.read_text(encoding="utf-8")
    assert conf.strip(), "config_helper produced an empty nginx.conf"
    return conf


def _wait_for_log(container, needle):
    """The upstream must be LISTENING before nginx is asked to proxy to it.

    Without this the first proxied request races `http.server`'s bind, comes
    back 502, and looks exactly like the partial-outage failure this suite is
    supposed to detect — a false positive on the one assertion that matters.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if needle in container.get_wrapped_container().logs().decode("utf-8", "replace"):
            return
        time.sleep(1)
    raise TimeoutError(f"{needle!r} never appeared within {STARTUP_TIMEOUT_SECONDS}s")


def _wait_until_serving(url):
    """Readiness is "answers HTTP", not "logged something".

    Waiting on nginx's log would be wrong here twice over: the generated config
    declares no `error_log`, so nginx's notices never reach stderr, and a config
    nginx REJECTS also produces no log — indistinguishable from one still
    booting. Polling the port fails loudly in both cases, which is the signal
    this suite exists to produce.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            return _get(url)
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    raise TimeoutError(f"nothing served {url} within {STARTUP_TIMEOUT_SECONDS}s")


def _get(url):
    """Returns (status, body). A 4xx/5xx is a result here, not an exception —
    asserting on 502 is the entire point of the outage case.
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


@pytest.fixture(scope="module")
def routed_stack(tmp_path_factory):
    """nginx wired to a live upstream and an absent one, on its own network.

    `vm-down` is deliberately never started — the whole suite exists to prove
    the healthy route survives its absence.
    """
    tmp_path = tmp_path_factory.mktemp("nginx-routing")
    conf = _render_nginx_conf(tmp_path)
    encoded_conf = base64.b64encode(conf.encode("utf-8")).decode("ascii")

    with Network() as network:
        live_vm = (
            DockerContainer(PYTHON_IMAGE)
            .with_network(network)
            .with_network_aliases(LIVE_ALIAS)
            # Answers with its own alias so a MIS-ROUTED request is visibly
            # wrong rather than merely non-200.
            #
            # `python3 -u` is load-bearing: http.server block-buffers its
            # "Serving HTTP" line when stdout is not a tty, so without it the
            # readiness log never reaches docker and the wait times out against
            # a container that is actually already serving.
            .with_command(
                [
                    "sh",
                    "-c",
                    f"mkdir -p /srv && echo '{LIVE_ALIAS}' >/srv/index.html && "
                    f"cd /srv && exec python3 -u -m http.server {LIVE_PORT}",
                ]
            )
        )
        with live_vm:
            _wait_for_log(live_vm, "Serving HTTP")

            nginx = (
                DockerContainer(NGINX_IMAGE)
                .with_network(network)
                .with_exposed_ports(NGINX_PORT)
                # Environment transfer works with remote daemons, unlike a
                # bind mount whose source would resolve on the daemon host.
                .with_env(NGINX_CONFIG_ENV, encoded_conf)
                .with_command(["sh", "-c", NGINX_BOOT_COMMAND])
            )
            with nginx:
                host = nginx.get_container_host_ip()
                port = nginx.get_exposed_port(NGINX_PORT)
                base_url = f"http://{host}:{port}"

                # An unrouted path is the cheapest readiness probe: it needs
                # nginx up but no upstream at all.
                _wait_until_serving(f"{base_url}/nope/")

                yield base_url


def test_nginx_starts_while_one_vm_is_absent(routed_stack):
    """The load-bearing case. With a literal upstream nginx would have exited on
    boot because vm-down does not resolve, and the fixture would never yield.
    """
    status, _ = _get(f"{routed_stack}/acme/live/")

    assert status == HTTP_OK


def test_a_healthy_terminal_reaches_its_own_vm(routed_stack):
    status, body = _get(f"{routed_stack}/acme/live/")

    assert status == HTTP_OK
    assert body.strip() == LIVE_ALIAS


def test_an_absent_vm_degrades_to_502(routed_stack):
    """Contained to its own route: a per-request 502, not an outage."""
    status, _ = _get(f"{routed_stack}/acme/down/")

    assert status == HTTP_BAD_GATEWAY


def test_the_healthy_route_survives_a_request_to_the_dead_one(routed_stack):
    """Proves nginx is genuinely alive after serving the failing route rather
    than having died and left a stale listening socket.
    """
    _get(f"{routed_stack}/acme/down/")

    status, body = _get(f"{routed_stack}/acme/live/")

    assert status == HTTP_OK
    assert body.strip() == LIVE_ALIAS


def test_an_unrouted_path_still_404s(routed_stack):
    status, _ = _get(f"{routed_stack}/nope/")

    assert status == HTTP_NOT_FOUND
