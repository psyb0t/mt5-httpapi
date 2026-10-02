"""Contract tests for POST /compile.

The endpoint takes caller-supplied MQL5 text, hands it to MetaEditor, and
returns a .ex5. Two classes of thing are worth pinning here:

  * The contract itself. Clients are written against it, and its two
    hard rules — `ok: true` implies a non-empty binary, and `log` is always a
    string — are the ones that break the caller silently if they regress.
  * The MetaEditor quirks the implementation exists to absorb: a UTF-16LE log,
    an exit code that goes non-zero on warnings, and a compiler that reports
    success in the log while producing no file.

MetaEditor itself is a Windows binary, so subprocess.run is patched. What is
NOT patched is the log decoding or the count parsing — those run against real
UTF-16LE bytes, because that is where the bugs live.
"""

import base64
import builtins
import json
import multiprocessing
import os
import pathlib
import threading
import time

import pytest

from mt5api import config
from mt5api.handlers import compile as compile_handler
from tests import compile_lock_worker


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utf16_log(text):
    """Bytes exactly as MetaEditor writes them: UTF-16LE with a BOM, CRLF."""
    return text.replace("\n", "\r\n").encode("utf-16")


SUCCESS_LOG = (
    "MetaEditor 5 build 4885 started\n"
    "ea.mq5 : information: compiling 'ea.mq5'\n"
    "Result: 0 errors, 0 warnings, 121 msec elapsed\n"
)

WARNING_LOG = (
    "ea.mq5(14,7) : warning 43: possible loss of data due to type conversion\n"
    "Result: 0 errors, 2 warnings, 138 msec elapsed\n"
)

ERROR_LOG = (
    "ea.mq5(12,5) : error 160: expression of 'void' type is illegal\n"
    "ea.mq5(13,1) : error 145: '}' - unexpected end of program\n"
    "ea.mq5(13,1) : error 100: ';' - semicolon expected\n"
    "Result: 3 errors, 0 warnings, 96 msec elapsed\n"
)

MISSING_INCLUDE_LOG = (
    "ea.mq5(3,11) : error 133: cannot open include file "
    "'SomeLibrary.mqh'\n"
    "Result: 1 errors, 0 warnings, 44 msec elapsed\n"
)


class FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode


@pytest.fixture
def compile_env(monkeypatch, tmp_path):
    """Point the handler at a temp workspace and a MetaEditor that 'exists'."""
    fake_editor = tmp_path / "MetaEditor64.exe"
    fake_editor.write_bytes(b"MZ")
    work = tmp_path / "work"
    monkeypatch.setattr(compile_handler, "COMPILE_METAEDITOR", str(fake_editor))
    monkeypatch.setattr(compile_handler, "COMPILE_WORK_DIR", str(work))
    monkeypatch.setattr(compile_handler, "COMPILE_INCLUDE_DIR", str(tmp_path / "MQL5"))
    monkeypatch.setattr(compile_handler, "COMPILE_TIMEOUT_SECONDS", 30)
    return {"work": work, "editor": str(fake_editor)}


def _fake_metaeditor(log_bytes, ex5_bytes=None, returncode=0, record=None):
    """Stand in for MetaEditor: writes the log it was given, and an .ex5 when
    the compile is meant to have produced one."""
    def _run(cmd, **kwargs):
        if record is not None:
            record.append(cmd)
        log_path = next(a.split(":", 1)[1] for a in cmd if a.startswith("/log:"))
        src_path = next(a.split(":", 1)[1] for a in cmd if a.startswith("/compile:"))
        with open(log_path, "wb") as fh:
            fh.write(log_bytes)
        if ex5_bytes is not None:
            with open(os.path.splitext(src_path)[0] + ".ex5", "wb") as fh:
                fh.write(ex5_bytes)
        return FakeCompleted(returncode)
    return _run


API_TOKEN = "full-api-token"
COMPILE_TOKEN = "compile-only-token"


@pytest.fixture
def client(monkeypatch):
    """Flask client with BOTH tokens configured, so the split is exercised."""
    from mt5api import server

    monkeypatch.setattr(server, "API_TOKEN", API_TOKEN)
    monkeypatch.setattr(server, "COMPILE_API_TOKEN", COMPILE_TOKEN)
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _post(client, body, token=COMPILE_TOKEN):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/compile", data=json.dumps(body), headers=headers)


# ── Log decoding and count parsing (no subprocess involved) ──────────────────

def test_metaeditor_log_is_decoded_as_utf16():
    # Decoding UTF-16LE as UTF-8 yields NUL-riddled mojibake and every count
    # regex silently stops matching, so this is the load-bearing decode.
    text = compile_handler._read_metaeditor_log_bytes(_utf16_log(SUCCESS_LOG))
    assert "Result: 0 errors, 0 warnings" in text
    assert "\x00" not in text


def test_counts_come_from_the_summary_line():
    assert compile_handler._parse_counts(ERROR_LOG) == (3, 0)
    assert compile_handler._parse_counts(WARNING_LOG) == (0, 2)
    assert compile_handler._parse_counts(SUCCESS_LOG) == (0, 0)


def test_counts_fall_back_to_counting_diagnostics_without_a_summary():
    no_summary = (
        "ea.mq5(12,5) : error 160: bad\n"
        "ea.mq5(13,1) : error 145: worse\n"
        "ea.mq5(14,2) : warning 43: meh\n"
    )
    assert compile_handler._parse_counts(no_summary) == (2, 1)


def test_log_tail_is_capped_at_8kb_and_never_none():
    assert compile_handler._tail(None) == ""
    assert compile_handler._tail("") == ""
    big = "x" * 20000
    assert len(compile_handler._tail(big).encode("utf-8")) <= 8192
    # Multi-byte content must not be cut mid-character.
    assert compile_handler._tail("é" * 20000).encode("utf-8")


# ── Filename handling: source text only, never a caller-supplied path ────────

@pytest.mark.parametrize("supplied,expected", [
    ("ea.mq5", "ea"),
    ("MyEA.mq5", "MyEA"),
    ("../../terminal64", "terminal64"),
    ("..\\..\\Windows\\system32\\evil.mq5", "evil"),
    ("C:\\Windows\\x.mq5", "x"),
    ("/etc/passwd", "passwd"),
    ("", "ea"),
    (None, "ea"),
    ("...", "ea"),
    ("a b;c&d.mq5", "a_b_c_d"),
])
def test_filename_is_reduced_to_a_harmless_stem(supplied, expected):
    assert compile_handler._safe_stem(supplied) == expected


# ── The contract ─────────────────────────────────────────────────────────────

def test_success_returns_the_binary_and_a_zero_warning_count(client, compile_env, monkeypatch):
    ex5 = b"\x00ex5-binary-content" * 40
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5),
    )
    resp = _post(client, {"source": "void OnTick(){}", "filename": "ea.mq5"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert base64.b64decode(body["ex5_base64"]) == ex5
    assert body["warnings"] == 0
    assert isinstance(body["log"], str)


def test_warnings_do_not_fail_the_compile(client, compile_env, monkeypatch):
    # MetaEditor exits NON-ZERO on warnings. Trusting the exit code here would
    # turn every warning into a failed compile.
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(WARNING_LOG), b"ex5", returncode=1),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert resp.get_json()["warnings"] == 2


def test_compile_errors_return_422_with_the_compiler_message(client, compile_env, monkeypatch):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(ERROR_LOG), None, returncode=1),
    )
    resp = _post(client, {"source": "garbage"})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["ok"] is False
    assert body["errors"] == 3
    assert "expression of 'void' type is illegal" in body["log"]
    assert "ex5_base64" not in body


def test_missing_include_is_a_compile_error_not_a_500(client, compile_env, monkeypatch):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(MISSING_INCLUDE_LOG), None, returncode=1),
    )
    resp = _post(client, {"source": "#include <SomeLibrary.mqh>"})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["errors"] == 1
    assert "cannot open include file" in body["log"]


def test_clean_log_without_a_binary_is_never_reported_as_success(client, compile_env, monkeypatch):
    # The one outcome the client cannot recover from: ok:true with no .ex5.
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), None, returncode=0),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["ok"] is False
    assert body["errors"] >= 1
    assert "produced no .ex5" in body["log"]


def test_empty_binary_is_not_success(client, compile_env, monkeypatch):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b""),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 422


def test_timeout_returns_504_with_a_string_log(client, compile_env, monkeypatch):
    def _timeout(cmd, **kwargs):
        raise compile_handler.subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(compile_handler.subprocess, "run", _timeout)
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 504
    body = resp.get_json()
    assert body["ok"] is False
    assert body["log"] == "compile timeout after 30s"


def test_missing_source_is_rejected_as_json(client, compile_env):
    for body in [{}, {"source": ""}, {"source": "   "}, {"source": 5}]:
        resp = _post(client, body)
        assert resp.status_code == 400
        assert isinstance(resp.get_json()["log"], str)


def test_missing_content_length_is_refused_with_411(client, compile_env):
    """request.content_length is None for a chunked request (no
    Content-Length declared) -- confirmed live against a real waitress
    server sending genuinely chunked HTTP (deep-qa audit). Without this
    check that skips the pre-parse 413 guard entirely and lets get_json()
    buffer an unbounded body before the decoded-source check downstream ever
    runs.

    Flask's test CLIENT recomputes Content-Length even when the environ key
    is deleted before client.open(), so this drives the view directly inside
    a request context with the header genuinely absent -- matching what a
    real chunked request looks like server-side -- rather than through
    client.post().
    """
    from flask import request

    from mt5api import server

    with server.app.test_request_context(
        "/compile", method="POST", data=b'{"source": "x"}',
        content_type="application/json",
    ):
        del request.environ["CONTENT_LENGTH"]
        assert request.content_length is None  # sanity: this IS the scenario

        resp_body, status = compile_handler.compile_source()

    assert status == 411
    assert "Content-Length" in resp_body.get_json()["log"]


def test_handler_never_raises_and_always_answers_json(client, compile_env, monkeypatch):
    def _boom(cmd, **kwargs):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(compile_handler.subprocess, "run", _boom)
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 500
    assert resp.is_json
    assert isinstance(resp.get_json()["log"], str)


def test_an_unexpected_error_does_not_leak_its_class_message_or_paths(
    client, compile_env, monkeypatch
):
    """The 500 body must be generic. The exception's class and message used to
    be echoed to the caller, which turned any provocable compiler or
    filesystem error into a readout of internal paths - the message of an
    OSError IS a path. Detail belongs in the server log only."""
    secret = "C:\\internal\\deploy\\path\\MetaEditor64.exe"

    def _boom(cmd, **kwargs):
        raise RuntimeError(f"kaboom at {secret}")
    monkeypatch.setattr(compile_handler.subprocess, "run", _boom)
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 500
    text = json.dumps(resp.get_json())
    assert "kaboom" not in text
    assert "RuntimeError" not in text
    assert secret.replace("\\", "\\\\") not in text and "MetaEditor64.exe" not in text


# ── Size limits ──────────────────────────────────────────────────────────────

def test_an_oversized_source_is_rejected_before_anything_is_written(
    client, compile_env, monkeypatch
):
    """The source cap bounds the disk write, so it must fire before the write:
    a 413 that arrives after the temp dir was populated bounds nothing."""
    monkeypatch.setattr(compile_handler, "COMPILE_MAX_SOURCE_BYTES", 1024)
    calls = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        lambda *a, **k: calls.append(a) or FakeCompleted(0),
    )
    resp = _post(client, {"source": "x" * 2048})
    assert resp.status_code == 413
    body = resp.get_json()
    assert body["ok"] is False
    assert "1024" in body["log"], "the refusal must name the limit"
    assert calls == [], "the compiler must never see an oversized source"
    assert not compile_env["work"].exists(), "nothing may reach the work dir"


def test_an_oversized_body_is_refused_from_its_declared_length_before_parsing(
    client, compile_env, monkeypatch
):
    """A body over the cap is 413 straight from Content-Length. The payload
    here is not even JSON: a 400 would prove the parser read it, a 413 proves
    it was refused unread."""
    monkeypatch.setattr(compile_handler, "COMPILE_MAX_SOURCE_BYTES", 1024)
    raw = b"x" * (4 * 1024 + 5000)  # over 4*cap + 4096 envelope slack
    resp = client.post(
        "/compile",
        data=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {COMPILE_TOKEN}",
        },
    )
    assert resp.status_code == 413
    assert isinstance(resp.get_json()["log"], str)


def test_a_source_within_the_cap_still_compiles(client, compile_env, monkeypatch):
    monkeypatch.setattr(compile_handler, "COMPILE_MAX_SOURCE_BYTES", 1024)
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"EX5"),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_an_oversized_artifact_is_refused_before_it_is_encoded(
    client, compile_env, monkeypatch
):
    """The output cap bounds memory and response size, so it is checked on
    disk: the refusal must carry no ex5_base64 at all, not a truncated one.
    And it is not a 422 - the caller's source compiled fine; the server is
    declining to return the result."""
    monkeypatch.setattr(compile_handler, "COMPILE_MAX_EX5_BYTES", 1024)
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"B" * 2048),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert "ex5_base64" not in body
    assert "1024" in body["log"], "the refusal must name the limit"


def test_an_artifact_within_the_cap_is_returned_whole(client, compile_env, monkeypatch):
    payload = b"B" * 512
    monkeypatch.setattr(compile_handler, "COMPILE_MAX_EX5_BYTES", 1024)
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=payload),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 200
    assert base64.b64decode(resp.get_json()["ex5_base64"]) == payload


# ── Temp directory hygiene ───────────────────────────────────────────────────

@pytest.mark.parametrize("log_text,ex5,expect_status", [
    (SUCCESS_LOG, b"ex5", 200),
    (ERROR_LOG, None, 422),
])
def test_temp_directory_is_removed_after_success_and_failure(
    client, compile_env, monkeypatch, log_text, ex5, expect_status
):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(log_text), ex5),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == expect_status
    work = compile_env["work"]
    leftovers = list(work.iterdir()) if work.exists() else []
    assert leftovers == [], f"temp dirs left behind: {leftovers}"


def test_temp_directory_is_removed_after_a_timeout(client, compile_env, monkeypatch):
    def _timeout(cmd, **kwargs):
        raise compile_handler.subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(compile_handler.subprocess, "run", _timeout)
    _post(client, {"source": "void OnTick(){}"})
    work = compile_env["work"]
    assert (list(work.iterdir()) if work.exists() else []) == []


# ── The compiler invocation itself ───────────────────────────────────────────

def test_caller_controls_neither_the_log_nor_the_include_argument(
    client, compile_env, monkeypatch
):
    recorded = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5", record=recorded),
    )
    _post(client, {
        "source": "void OnTick(){}",
        "filename": "ea.mq5",
        "log": "C:\\evil.log",
        "include": "C:\\evil",
    })
    cmd = recorded[0]
    log_arg = next(a for a in cmd if a.startswith("/log:"))
    inc_arg = next(a for a in cmd if a.startswith("/inc:"))
    assert "evil" not in log_arg
    assert "evil" not in inc_arg
    assert inc_arg == f"/inc:{compile_handler.COMPILE_INCLUDE_DIR}"
    # The source compiled is the one we wrote, inside the temp dir.
    src_arg = next(a for a in cmd if a.startswith("/compile:"))
    assert src_arg.endswith("ea.mq5")
    assert str(compile_env["work"]) in src_arg


# ── Auth: a compile-only credential that opens nothing else ──────────────────

def test_compile_accepts_the_compile_token(client, compile_env, monkeypatch):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )
    assert _post(client, {"source": "void OnTick(){}"}, token=COMPILE_TOKEN).status_code == 200


def test_compile_also_accepts_the_existing_api_token(client, compile_env, monkeypatch):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )
    assert _post(client, {"source": "void OnTick(){}"}, token=API_TOKEN).status_code == 200


@pytest.mark.parametrize("token", [None, "", "wrong-token", "Bearer-ish"])
def test_compile_rejects_bad_or_missing_credentials(client, compile_env, token):
    resp = _post(client, {"source": "void OnTick(){}"}, token=token)
    assert resp.status_code in (401, 403)
    # Even the auth failure answers JSON: a non-JSON body means "broken host"
    # to this client, which would send it down a different recovery path.
    assert resp.is_json


@pytest.mark.parametrize("method,path", [
    ("get", "/account"),
    ("get", "/positions"),
    ("get", "/orders"),
    ("post", "/orders"),
    ("post", "/terminal/restart"),
    ("get", "/terminal"),
])
def test_compile_token_is_refused_on_every_other_route(client, method, path):
    """The whole point of the second credential.

    The existing token opens order placement, position management and terminal
    restart. A caller that only needs to compile must not be able to reach
    any of it, so the compile token has to fail CLOSED everywhere else — and
    the trade routes are the ones that matter.
    """
    resp = getattr(client, method)(
        path, headers={"Authorization": f"Bearer {COMPILE_TOKEN}"}
    )
    assert resp.status_code == 401, f"{method.upper()} {path} accepted the compile token"


def test_the_full_api_token_still_works_on_other_routes(client, monkeypatch):
    """The existing gate must be unchanged for existing routes."""
    resp = client.get("/ping", headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert resp.status_code == 200


def test_other_routes_still_reject_a_missing_token(client):
    assert client.get("/ping").status_code == 401


# ── Local toolchain mirror ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_toolchain_cache(monkeypatch):
    """The mirror resolves once per process; tests must not inherit each
    other's resolution."""
    monkeypatch.setattr(compile_handler, "_LOCAL_TOOLCHAIN", None)
    monkeypatch.setattr(compile_handler, "_LOCAL_TOOLCHAIN_RESOLVED", False)


def test_no_mirror_configured_uses_the_shared_toolchain(client, compile_env, monkeypatch):
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    recorded = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5", record=recorded),
    )
    _post(client, {"source": "void OnTick(){}"})
    assert recorded[0][0] == compile_env["editor"]


def test_mirror_copies_the_toolchain_and_compiles_from_it(
    client, compile_env, monkeypatch, tmp_path
):
    # The shared "terminal dir" gets an include tree and a Config dir alongside
    # MetaEditor, mirroring the real layout.
    shared = tmp_path
    (shared / "MQL5" / "Include").mkdir(parents=True)
    (shared / "MQL5" / "Include" / "Lib.mqh").write_text("// lib")
    (shared / "Config").mkdir()
    cache = tmp_path / "local-cache"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))

    recorded = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5", record=recorded),
    )
    assert _post(client, {"source": "void OnTick(){}"}).status_code == 200

    # Compiled from the mirror, not the shared copy.
    assert recorded[0][0] == str(cache / "MetaEditor64.exe")
    inc = next(a for a in recorded[0] if a.startswith("/inc:"))
    assert inc == f"/inc:{cache / 'MQL5'}"
    # Includes came along, so a compile never reaches back across the mount.
    assert (cache / "MQL5" / "Include" / "Lib.mqh").read_text() == "// lib"
    # And only what a compile needs: no terminal64.exe, no Bases.
    assert not (cache / "terminal64.exe").exists()


def test_mirror_is_built_once_per_process(client, compile_env, monkeypatch, tmp_path):
    cache = tmp_path / "cache-once"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    copies = []
    real_copy = compile_handler.shutil.copy2
    monkeypatch.setattr(
        compile_handler.shutil, "copy2",
        lambda *a, **k: (copies.append(a[0]), real_copy(*a, **k))[1],
    )
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )
    for _ in range(3):
        _post(client, {"source": "void OnTick(){}"})
    # 105MB does not get copied on every request.
    assert len(copies) == 1, f"toolchain copied {len(copies)} times"


def test_a_warm_mirror_is_not_re_copied_on_the_next_process_start(
    client, compile_env, monkeypatch, tmp_path
):
    """The regression that matters after a restart.

    The mirror is built once per PROCESS, but the VM restarts several times a
    day and the mirror survives on local disk. Re-copying the whole tree each
    time puts that cost in front of the first compile after every restart, and
    the stock MQL5 Include tree is ~260 files - big enough on a slow mount to
    push that first caller past a reverse-proxy timeout.
    """
    shared = tmp_path
    include = shared / "MQL5" / "Include" / "Trade"
    include.mkdir(parents=True)
    for name in ("Trade.mqh", "SymbolInfo.mqh", "PositionInfo.mqh"):
        (include / name).write_text(f"// {name}")
    (shared / "Config").mkdir()
    cache = tmp_path / "warm-cache"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )

    # First process: builds the mirror.
    assert _post(client, {"source": "void OnTick(){}"}).status_code == 200
    assert (cache / "MQL5" / "Include" / "Trade" / "Trade.mqh").exists()

    # Second process, same on-disk mirror: nothing should be copied again.
    compile_handler._LOCAL_TOOLCHAIN = None
    compile_handler._LOCAL_TOOLCHAIN_RESOLVED = False
    copies = []
    real_copy = compile_handler.shutil.copy2
    monkeypatch.setattr(
        compile_handler.shutil, "copy2",
        lambda *a, **k: (copies.append(a[0]), real_copy(*a, **k))[1],
    )
    assert _post(client, {"source": "void OnTick(){}"}).status_code == 200
    assert copies == [], f"re-copied {len(copies)} unchanged file(s) on restart"


def test_a_changed_include_is_still_picked_up_by_the_mirror(
    client, compile_env, monkeypatch, tmp_path
):
    """Skipping unchanged files must not mean serving a stale library.

    An edited .mqh that compiles clean but is a version behind is worse than a
    slow compile, so the staleness check has to actually notice the edit.
    """
    shared = tmp_path
    include = shared / "MQL5" / "Include"
    include.mkdir(parents=True)
    (include / "Lib.mqh").write_text("// v1")
    (shared / "Config").mkdir()
    cache = tmp_path / "refresh-cache"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )
    _post(client, {"source": "void OnTick(){}"})
    assert (cache / "MQL5" / "Include" / "Lib.mqh").read_text() == "// v1"

    # Edit the source library, then restart. Size and mtime both move.
    (include / "Lib.mqh").write_text("// v2 is longer than v1")
    os.utime(include / "Lib.mqh", (time.time() + 10, time.time() + 10))
    compile_handler._LOCAL_TOOLCHAIN = None
    compile_handler._LOCAL_TOOLCHAIN_RESOLVED = False

    _post(client, {"source": "void OnTick(){}"})
    assert (cache / "MQL5" / "Include" / "Lib.mqh").read_text() == "// v2 is longer than v1"


def test_a_broken_mirror_falls_back_instead_of_failing_the_compile(
    client, compile_env, monkeypatch, tmp_path
):
    # A compile that works slowly beats one that stops working because a cache
    # directory was not writable.
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(tmp_path / "nope"))
    monkeypatch.setattr(
        compile_handler.shutil, "copy2",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    recorded = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5", record=recorded),
    )
    resp = _post(client, {"source": "void OnTick(){}"})
    assert resp.status_code == 200
    assert recorded[0][0] == compile_env["editor"]


def test_an_edited_include_reaches_later_compiles_without_a_restart(
    client, compile_env, monkeypatch, tmp_path
):
    """A shared header is edited while the process keeps running.

    The mirror is otherwise resolved once per process, so without a periodic
    re-check the old copy is used until the next restart - and the compile that
    used it still returns ok:true. A silently stale binary is the worst outcome
    available here: nothing downstream can distinguish it from a correct one.
    """
    shared = tmp_path
    include = shared / "MQL5" / "Include"
    include.mkdir(parents=True)
    (include / "Lib.mqh").write_text("// v1")
    (shared / "Config").mkdir()
    cache = tmp_path / "refresh-live"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "INCLUDE_REFRESH_SECONDS", 0)
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )

    _post(client, {"source": "void OnTick(){}"})
    assert (cache / "MQL5" / "Include" / "Lib.mqh").read_text() == "// v1"

    # Edit the shared header. No restart, no cache reset.
    (include / "Lib.mqh").write_text("// v2 and longer")
    os.utime(include / "Lib.mqh", (time.time() + 5, time.time() + 5))

    _post(client, {"source": "void OnTick(){}"})
    assert (cache / "MQL5" / "Include" / "Lib.mqh").read_text() == "// v2 and longer"


def test_the_include_recheck_is_rate_limited(client, compile_env, monkeypatch, tmp_path):
    """The re-check walks the source tree, which sits on the slow mount.

    Doing that on every compile would put the walk in front of every caller, so
    it is bounded by INCLUDE_REFRESH_SECONDS rather than run each time.
    """
    shared = tmp_path
    (shared / "MQL5" / "Include").mkdir(parents=True)
    (shared / "MQL5" / "Include" / "Lib.mqh").write_text("// v1")
    (shared / "Config").mkdir()
    cache = tmp_path / "ratelimit"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "INCLUDE_REFRESH_SECONDS", 3600)
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )
    _post(client, {"source": "void OnTick(){}"})

    walks = []
    real_walk = compile_handler.os.walk
    monkeypatch.setattr(
        compile_handler.os, "walk",
        lambda *a, **k: (walks.append(a[0]), real_walk(*a, **k))[1],
    )
    for _ in range(5):
        _post(client, {"source": "void OnTick(){}"})
    # The mirror itself (local disk) is stat'ed for include_hash on every
    # compile; the source tree on the slow mount must not be.
    source_walks = [w for w in walks if str(w).startswith(str(shared / "MQL5"))]
    assert source_walks == [], (
        f"re-walked the source include tree {len(source_walks)} time(s) inside the window"
    )


# ── include_hash: which library was this binary built against? ───────────────

def _hash_env(monkeypatch, tmp_path, name="hash-cache"):
    """Shared toolchain + mirror, with one header in the include tree."""
    shared = tmp_path
    include = shared / "MQL5" / "Include"
    include.mkdir(parents=True, exist_ok=True)
    (include / "Lib.mqh").write_text("// v1")
    (shared / "Config").mkdir(exist_ok=True)
    cache = tmp_path / name
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "INCLUDE_REFRESH_SECONDS", 0)
    monkeypatch.setattr(compile_handler, "_INCLUDE_HASH", None)
    monkeypatch.setattr(compile_handler, "_INCLUDE_HASH_KEY", None)
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )
    return include


def test_success_reports_the_include_hash(client, compile_env, monkeypatch, tmp_path):
    _hash_env(monkeypatch, tmp_path)
    body = _post(client, {"source": "void OnTick(){}"}).get_json()
    assert body["include_hash"].startswith("sha256:")
    assert len(body["include_hash"]) == len("sha256:") + 64


def test_the_include_hash_is_stable_across_compiles(client, compile_env, monkeypatch, tmp_path):
    """Two builds against an unchanged library must be comparable."""
    _hash_env(monkeypatch, tmp_path)
    first = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    second = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    assert first == second


def test_editing_a_header_changes_the_include_hash(client, compile_env, monkeypatch, tmp_path):
    """The whole point: drift has to be detectable after the fact."""
    include = _hash_env(monkeypatch, tmp_path)
    before = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]

    (include / "Lib.mqh").write_text("// v2 is different")
    os.utime(include / "Lib.mqh", (time.time() + 5, time.time() + 5))

    after = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    assert after != before, "an edited header left the include hash unchanged"


def test_adding_a_header_changes_the_include_hash(client, compile_env, monkeypatch, tmp_path):
    """Contents alone would miss this - the digest covers paths too."""
    include = _hash_env(monkeypatch, tmp_path)
    before = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]

    (include / "Extra.mqh").write_text("// new")
    after = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    assert after != before, "a new header left the include hash unchanged"


def test_the_hash_describes_the_mirror_not_the_source(client, compile_env, monkeypatch, tmp_path):
    """If the mirror is stale, the hash must report what was COMPILED.

    Hashing the source instead would assert the build used a library it did not,
    which is worse than reporting no hash at all.
    """
    include = _hash_env(monkeypatch, tmp_path, name="mirror-truth")
    body = _post(client, {"source": "void OnTick(){}"}).get_json()
    mirrored = body["include_hash"]

    # Change the source but freeze the mirror by disabling any further refresh.
    monkeypatch.setattr(compile_handler, "INCLUDE_REFRESH_SECONDS", 3600)
    monkeypatch.setattr(compile_handler, "_INCLUDES_CHECKED_AT", time.monotonic())
    (include / "Lib.mqh").write_text("// source moved on without the mirror")
    os.utime(include / "Lib.mqh", (time.time() + 9, time.time() + 9))

    again = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    assert again == mirrored, "hash followed the source instead of the compiled tree"


# ── Boot warm-up ─────────────────────────────────────────────────────────────
#
# The warm-up exists to move MetaEditor's cold load off the first real caller.
# Its gates matter more than the compile it runs: ungated, every API process on
# the VM launches its own MetaEditor at boot.

def test_warmup_does_nothing_without_a_local_cache(monkeypatch):
    """No mirror means no cold-load problem worth a background compile."""
    monkeypatch.setattr(compile_handler, "WARMUP_ENABLED", False)
    started = []
    monkeypatch.setattr(
        compile_handler.threading, "Thread",
        lambda *a, **k: started.append(k) or pytest.fail("started a thread"),
    )
    compile_handler.start_warmup()
    assert started == []


def test_only_one_process_claims_the_warmup(monkeypatch, tmp_path):
    """The regression that matters.

    Every API process on the VM runs this code and they share the cache dir, so
    the claim has to be exclusive ACROSS processes - twenty MetaEditors starting
    together would recreate the CPU saturation the warm-up is meant to avoid.
    """
    cache = tmp_path / "claim"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    winners = [compile_handler._claim_warmup() for _ in range(20)]
    assert winners.count(True) == 1, f"{winners.count(True)} processes claimed the warm-up"


def test_a_claim_from_an_earlier_boot_does_not_block_this_boots_warmup(monkeypatch, tmp_path):
    """The cache survives reboots, so a claim must only count for the boot
    that made it. With reboot_interval at 30 minutes, the old one-hour expiry
    skipped the warm-up on every other boot."""
    cache = tmp_path / "boots"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "_boot_time", lambda: 1_000_000)
    assert compile_handler._claim_warmup() is True
    assert compile_handler._claim_warmup() is False, "claimed twice in one boot"

    # 30 minutes later the VM reboots; the claim file is only seconds old.
    monkeypatch.setattr(compile_handler, "_boot_time", lambda: 1_000_000 + 1800)
    assert compile_handler._claim_warmup() is True, "the previous boot's claim blocked warm-up"
    assert compile_handler._claim_warmup() is False


def test_boot_time_jitter_does_not_look_like_a_reboot(monkeypatch, tmp_path):
    """Windows derives boot time from the uptime counter, so two processes of
    one boot can read it a second apart."""
    cache = tmp_path / "jitter"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "_boot_time", lambda: 1_000_000)
    assert compile_handler._claim_warmup() is True
    monkeypatch.setattr(compile_handler, "_boot_time", lambda: 1_000_002)
    assert compile_handler._claim_warmup() is False


def test_a_claim_in_the_old_format_is_replaced(monkeypatch, tmp_path):
    cache = tmp_path / "garbage"
    cache.mkdir()
    (cache / compile_handler._WARMUP_CLAIM).write_text("2026-09-29T10:00:00")
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "_boot_time", lambda: 1_000_000)
    # The timestamp an earlier version wrote cannot be this boot's claim.
    assert compile_handler._claim_warmup() is True
    assert compile_handler._claim_warmup() is False


def test_warmup_yields_to_a_real_compile(monkeypatch, tmp_path):
    """A caller must never queue behind a warm-up - that inverts its purpose."""
    cache = tmp_path / "yield"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "WARMUP_DELAY_SECONDS", 0)
    ran = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        lambda *a, **k: ran.append(a) or FakeCompleted(0),
    )

    compile_handler._COMPILE_LOCK.acquire()  # stand in for a compile in flight
    try:
        compile_handler._warmup()
    finally:
        compile_handler._COMPILE_LOCK.release()

    assert ran == [], "warm-up ran MetaEditor while a compile held the lock"


def test_a_failing_warmup_is_swallowed(monkeypatch, tmp_path):
    """Warm-up is an optimisation. It must never take the process down."""
    cache = tmp_path / "boom"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    monkeypatch.setattr(compile_handler, "WARMUP_DELAY_SECONDS", 0)
    monkeypatch.setattr(compile_handler, "COMPILE_METAEDITOR", str(tmp_path / "me.exe"))
    (tmp_path / "me.exe").write_bytes(b"MZ")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("cannot launch")),
    )
    compile_handler._warmup()  # must not raise
    # And the lock is released, so real compiles still work afterwards.
    assert compile_handler._COMPILE_LOCK.acquire(blocking=False)
    compile_handler._COMPILE_LOCK.release()


def test_a_header_deleted_from_source_is_pruned_from_the_mirror(
    client, compile_env, monkeypatch, tmp_path
):
    """Copy-only leaves a one-way mirror.

    A header removed from the source stayed in the mirror and kept resolving,
    so `#include <Gone.mqh>` still compiled against a file nobody maintains.
    Found in production: a test header deleted from the source was still being
    included by the compiler hours later.
    """
    include = _hash_env(monkeypatch, tmp_path, name="prune")
    (include / "Doomed.mqh").write_text("// remove me")
    cache = tmp_path / "prune"
    _post(client, {"source": "void OnTick(){}"})
    assert (cache / "MQL5" / "Include" / "Doomed.mqh").exists()

    os.remove(include / "Doomed.mqh")
    _post(client, {"source": "void OnTick(){}"})

    assert not (cache / "MQL5" / "Include" / "Doomed.mqh").exists(), \
        "a header deleted from source survived in the mirror"


def test_pruning_is_skipped_when_the_source_is_unreachable(monkeypatch, tmp_path):
    """A transient mount failure must not wipe the mirror.

    The source walk yields nothing when the share is down; pruning against that
    empty set would delete every mirrored header and turn a blip into an outage.
    """
    src = tmp_path / "gone"          # never created
    dst = tmp_path / "mirror"
    (dst / "Include").mkdir(parents=True)
    (dst / "Include" / "Keep.mqh").write_text("// precious")

    copied, removed = compile_handler._mirror_tree(str(src), str(dst))

    assert (copied, removed) == (0, 0)
    assert (dst / "Include" / "Keep.mqh").exists(), "pruned the mirror against an empty source"


# ── include_files: WHICH header moved, not just that something did ───────────

def _digest_env(monkeypatch, tmp_path, patterns="Mine*.mqh", name="perfile"):
    include = _hash_env(monkeypatch, tmp_path, name=name)
    (include / "MineLicense.mqh").write_text("// licence v1")
    (include / "Stock.mqh").write_text("// vendor library")
    monkeypatch.setattr(
        compile_handler, "_INCLUDE_DIGEST_PATTERNS",
        tuple(p.strip() for p in patterns.split(",") if p.strip()),
    )
    monkeypatch.setattr(compile_handler, "_INCLUDE_FILES", None)
    return include


def test_include_files_reports_only_the_configured_headers(
    client, compile_env, monkeypatch, tmp_path
):
    _digest_env(monkeypatch, tmp_path)
    body = _post(client, {"source": "void OnTick(){}"}).get_json()
    assert set(body["include_files"]) == {"MineLicense.mqh"}
    assert body["include_files"]["MineLicense.mqh"].startswith("sha256:")


def test_include_files_is_absent_when_nothing_is_configured(
    client, compile_env, monkeypatch, tmp_path
):
    """Default must stay off: ~260 digests per response is a payload, not an answer."""
    _digest_env(monkeypatch, tmp_path, patterns="", name="unconfigured")
    body = _post(client, {"source": "void OnTick(){}"}).get_json()
    assert "include_files" not in body
    assert body["include_hash"].startswith("sha256:")


def test_a_stock_library_change_moves_the_tree_hash_but_not_our_header(
    client, compile_env, monkeypatch, tmp_path
):
    """The whole point of the field.

    A MetaTrader upgrade and a licence-header edit both move the tree hash. If
    the per-file digest is unchanged, the caller knows the second did not
    happen and can skip rebuilding every dependent artifact.
    """
    include = _digest_env(monkeypatch, tmp_path, name="stockmove")
    first = _post(client, {"source": "void OnTick(){}"}).get_json()

    (include / "Stock.mqh").write_text("// vendor library, upgraded and longer")
    os.utime(include / "Stock.mqh", (time.time() + 5, time.time() + 5))
    second = _post(client, {"source": "void OnTick(){}"}).get_json()

    assert second["include_hash"] != first["include_hash"], "tree hash missed a change"
    assert second["include_files"] == first["include_files"], \
        "a stock-library change moved our header's digest"


def test_editing_our_header_moves_its_own_digest(client, compile_env, monkeypatch, tmp_path):
    include = _digest_env(monkeypatch, tmp_path, name="ourmove")
    first = _post(client, {"source": "void OnTick(){}"}).get_json()

    (include / "MineLicense.mqh").write_text("// licence v2, materially different")
    os.utime(include / "MineLicense.mqh", (time.time() + 5, time.time() + 5))
    second = _post(client, {"source": "void OnTick(){}"}).get_json()

    assert second["include_files"]["MineLicense.mqh"] != first["include_files"]["MineLicense.mqh"]


def test_a_configured_header_missing_from_the_tree_is_absent_not_null(
    client, compile_env, monkeypatch, tmp_path
):
    """Absent means "not in the tree the compiler read" - the thing worth
    knowing before a rebuild. A null would blur that with "unreadable"."""
    _digest_env(monkeypatch, tmp_path, patterns="Mine*.mqh,NeverExisted.mqh", name="absent")
    body = _post(client, {"source": "void OnTick(){}"}).get_json()
    assert "NeverExisted.mqh" not in body["include_files"]
    assert body["include_files"]["MineLicense.mqh"].startswith("sha256:")


# ── Cross-process compile serialization ───────────────────────────────────
#
# MetaEditor is single-instance per installation directory, but _COMPILE_LOCK
# is a threading.Lock: it only serializes calls inside ONE process. Every
# mt5api process on a VM exposes /compile and, by default, all of them
# resolve to the SAME installation directory. The cross-process lock is an OS
# byte-range lock (flock here, msvcrt on Windows), which the OS releases when
# its holder's handle closes or the process dies. There is no stale window,
# heartbeat or reaper left to get wrong.


@pytest.fixture
def lock_env(monkeypatch, tmp_path):
    """No local cache, so the lock sits beside the configured MetaEditor."""
    editor = tmp_path / "MetaEditor64.exe"
    editor.write_bytes(b"MZ")
    monkeypatch.setattr(compile_handler, "COMPILE_METAEDITOR", str(editor))
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    return tmp_path


def test_cross_process_lock_is_exclusive(lock_env):
    far_future = time.monotonic() + 60

    held = compile_handler._acquire_cross_process_lock(far_future)
    assert held is not None

    # Already held: a deadline in the past must fail fast, not block for the
    # full budget above.
    assert compile_handler._acquire_cross_process_lock(time.monotonic()) is None

    compile_handler._release_cross_process_lock(held)

    reacquired = compile_handler._acquire_cross_process_lock(far_future)
    assert reacquired is not None
    compile_handler._release_cross_process_lock(reacquired)


def test_the_lock_covers_the_local_cache_when_one_is_configured(lock_env, monkeypatch):
    """The mirror is refreshed under this lock, so the lock has to sit with
    the mirror every process shares, not with whichever copy one process
    ended up compiling from."""
    cache = lock_env / "cache"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    assert compile_handler._cross_process_lock_path() == str(
        cache / compile_handler._CROSS_PROCESS_LOCK_BASENAME
    )


def test_an_old_lock_file_is_not_a_held_lock(lock_env):
    """Holding is the OS lock, not the file. A file left behind by an earlier
    version, or by a holder that died, blocks nobody."""
    path = pathlib.Path(compile_handler._cross_process_lock_path())
    path.write_text("deadbeef" * 4)
    old = time.time() - 86400
    os.utime(path, (old, old))

    held = compile_handler._acquire_cross_process_lock(time.monotonic())
    assert held is not None
    compile_handler._release_cross_process_lock(held)
    assert path.exists(), "the lock file must never be deleted"


def test_a_long_hold_is_never_taken_over(lock_env):
    """Review item 2 and 3. The old lock was judged by the file's mtime: a
    holder whose heartbeat stopped (one failed read ended it for good) was
    reaped 60s later mid-compile, and the rename-based reaper could strand a
    live lock. Now age means nothing: however old the file looks, a live
    holder keeps the lock until it lets go."""
    held = compile_handler._acquire_cross_process_lock(time.monotonic() + 5)
    assert held is not None
    ancient = time.time() - 86400
    os.utime(held.path, (ancient, ancient))
    try:
        assert compile_handler._acquire_cross_process_lock(time.monotonic() + 0.5) is None
    finally:
        compile_handler._release_cross_process_lock(held)


def test_a_real_compile_waits_for_the_cross_process_lock_then_proceeds(
    client, compile_env, monkeypatch
):
    """HTTP-level: proves POST /compile itself engages the cross-process lock
    (not just that the helper functions work in isolation)."""
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(compile_handler.subprocess, "run", _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary"))

    held = compile_handler._acquire_cross_process_lock(time.monotonic() + 60)
    assert held is not None

    result = {}

    def _call():
        result["response"] = _post(client, {"source": "void OnTick(){}"})

    t = threading.Thread(target=_call)
    t.start()
    t.join(timeout=1)
    assert t.is_alive(), "the request completed without waiting for the held cross-process lock"

    compile_handler._release_cross_process_lock(held)
    t.join(timeout=5)
    assert not t.is_alive()

    body = result["response"].get_json()
    assert result["response"].status_code == 200
    assert body["ok"] is True


def test_compile_returns_504_when_the_cross_process_lock_never_frees(
    client, compile_env, monkeypatch
):
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(compile_handler, "COMPILE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(compile_handler, "_LOCK_WAIT_MARGIN_SECONDS", 0)

    held = compile_handler._acquire_cross_process_lock(time.monotonic() + 60)
    assert held is not None
    try:
        resp = _post(client, {"source": "void OnTick(){}"})
        assert resp.status_code == 504
        assert "compiler lock" in resp.get_json()["log"]
    finally:
        compile_handler._release_cross_process_lock(held)


def test_the_mirror_is_refreshed_inside_the_cross_process_lock(
    client, compile_env, monkeypatch, tmp_path
):
    """Review item 7. Refreshing the mirror copies into and prunes the shared
    include tree, so it must not run while another process's MetaEditor is
    reading it: _local_toolchain() has to run with the lock already held."""
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(tmp_path / "mirror"))
    seen = []

    def _probe_toolchain():
        # Another process trying now must be shut out.
        other = compile_handler._acquire_cross_process_lock(time.monotonic())
        seen.append(other is None)
        if other is not None:
            compile_handler._release_cross_process_lock(other)
        return None

    monkeypatch.setattr(compile_handler, "_local_toolchain", _probe_toolchain)
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary"),
    )
    assert _post(client, {"source": "void OnTick(){}"}).status_code == 200
    assert seen == [True], "the mirror was touched without the cross-process lock"


def test_the_warmup_refreshes_the_mirror_inside_the_cross_process_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(tmp_path / "mirror"))
    monkeypatch.setattr(compile_handler, "WARMUP_DELAY_SECONDS", 0)
    monkeypatch.setattr(compile_handler, "_boot_time", lambda: 1)
    monkeypatch.setattr(compile_handler, "COMPILE_WORK_DIR", str(tmp_path / "work"))
    seen = []

    def _probe_toolchain():
        other = compile_handler._acquire_cross_process_lock(time.monotonic())
        seen.append(other is None)
        if other is not None:
            compile_handler._release_cross_process_lock(other)
        return None

    monkeypatch.setattr(compile_handler, "_local_toolchain", _probe_toolchain)
    monkeypatch.setattr(compile_handler, "COMPILE_METAEDITOR", str(tmp_path / "missing.exe"))
    compile_handler._warmup()
    assert seen == [True]
    # Released afterwards, whatever the warm-up did.
    again = compile_handler._acquire_cross_process_lock(time.monotonic())
    assert again is not None
    compile_handler._release_cross_process_lock(again)


def test_cross_process_lock_logs_unexpected_errors_not_just_contention(
    monkeypatch, lock_env
):
    """Contention is ordinary and silent. Anything else (permission denied, a
    bad path, a share that refuses locks) means the lock mechanism itself is
    broken and every compile would report a generic "busy" forever - that has
    to be visible in the log."""
    warnings = []
    monkeypatch.setattr(
        compile_handler.log, "warning", lambda *a, **k: warnings.append((a, k))
    )

    lock_path = compile_handler._cross_process_lock_path()
    real_open = os.open

    def _flaky_open(path, flags, mode=0o777):
        if path == lock_path:
            raise PermissionError(13, "simulated: lock dir not writable")
        return real_open(path, flags, mode)

    monkeypatch.setattr(compile_handler.os, "open", _flaky_open)

    assert compile_handler._acquire_cross_process_lock(time.monotonic() + 0.3) is None
    assert any("unusable" in str(args) for args, _kwargs in warnings)


def test_contention_itself_is_not_logged(monkeypatch, lock_env):
    warnings = []
    monkeypatch.setattr(
        compile_handler.log, "warning", lambda *a, **k: warnings.append((a, k))
    )
    held = compile_handler._acquire_cross_process_lock(time.monotonic() + 5)
    try:
        assert compile_handler._acquire_cross_process_lock(time.monotonic() + 0.3) is None
    finally:
        compile_handler._release_cross_process_lock(held)
    assert warnings == []


# ── Real multi-process mutual exclusion ──────────────────────────────


def _run_lock_race(tmp_path, monkeypatch, workers, hold, die_holding=False):
    """Start `workers` real interpreters that all grab the compile lock at once,
    and return their (pid, entered, exited) intervals."""
    ctx = multiprocessing.get_context("spawn")
    editor = tmp_path / "MetaEditor64.exe"
    editor.write_bytes(b"MZ")

    monkeypatch.setenv("MT5_REPO_ROOT", str(pathlib.Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("COMPILE_TERMINAL_DIR", str(tmp_path))
    monkeypatch.delenv("COMPILE_LOCAL_CACHE", raising=False)
    with ctx.Manager() as manager:
        results = manager.list()
        barrier = manager.Barrier(workers)
        procs = [
            ctx.Process(
                target=compile_lock_worker.run,
                args=(hold, barrier, results, die_holding and index == 0),
            )
            for index in range(workers)
        ]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(timeout=180)
        intervals = sorted(list(results), key=lambda row: row[1])
        exit_codes = [proc.exitcode for proc in procs]

    assert all(row[0] != "timeout" for row in intervals), f"a worker never got the lock: {intervals}"
    return intervals, exit_codes


def _assert_no_overlap(intervals, hold):
    for (pid_a, _start_a, end_a), (pid_b, start_b, _end_b) in zip(intervals, intervals[1:]):
        assert end_a <= start_b, (
            f"pid {pid_a} still held the lock when pid {pid_b} entered "
            f"({end_a:.3f} > {start_b:.3f}) -- two MetaEditors could run at once"
        )
    span = intervals[-1][2] - intervals[0][1]
    assert span >= hold * len(intervals) * 0.9, f"workers overlapped: span {span:.2f}s"


def test_separate_processes_never_hold_the_compile_lock_at_the_same_time(
    tmp_path, monkeypatch
):
    """Real OS processes, started together on a barrier, each recording when it
    entered and left the critical section. Any overlap means two MetaEditors
    could have run concurrently."""
    hold = 0.4
    intervals, exit_codes = _run_lock_race(tmp_path, monkeypatch, workers=4, hold=hold)
    assert exit_codes == [0, 0, 0, 0]
    assert len(intervals) == 4
    _assert_no_overlap(intervals, hold)


def test_a_holder_killed_mid_compile_releases_the_lock_at_once(tmp_path, monkeypatch):
    """The case the stale window used to cover. The first worker takes the
    lock and is killed while holding it (os._exit, no release). The others
    must get the lock as soon as it dies - not after a stale window - and
    still one at a time."""
    hold = 0.4
    started = time.monotonic()
    intervals, exit_codes = _run_lock_race(
        tmp_path, monkeypatch, workers=3, hold=hold, die_holding=True
    )
    assert sorted(exit_codes) == [0, 0, 9]
    assert len(intervals) == 2, intervals
    _assert_no_overlap(intervals, hold)
    assert time.monotonic() - started < 30, "waiters sat out a stale window"


# ── Bounded waiting (review item 1) ──────────────────────────────────


def test_waiting_compiles_are_bounded_and_the_rest_get_429(client, compile_env, monkeypatch):
    """A compile waiting on the lock holds a waitress thread. With only the
    compile token, a caller must not be able to park the whole pool: past
    MAX_WAITING_COMPILES waiters the answer is an immediate 429."""
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary"),
    )
    compile_handler._COMPILE_LOCK.acquire()  # a compile in flight
    waiters = []
    try:
        # The running compile's slot is taken by the stand-in above only in
        # spirit; fill every slot with real requests that block on the lock.
        slots = 1 + compile_handler.MAX_WAITING_COMPILES
        for _ in range(slots):
            thread = threading.Thread(
                target=lambda: waiters.append(_post(client, {"source": "void OnTick(){}"}))
            )
            thread.start()
        deadline = time.monotonic() + 5
        while compile_handler._COMPILE_SLOTS._value and time.monotonic() < deadline:
            time.sleep(0.01)
        assert compile_handler._COMPILE_SLOTS._value == 0

        started = time.monotonic()
        refused = _post(client, {"source": "void OnTick(){}"})
        assert refused.status_code == 429
        assert refused.headers["Retry-After"] == str(compile_handler._BUSY_RETRY_AFTER_SECONDS)
        assert refused.get_json()["ok"] is False
        assert time.monotonic() - started < 1, "the refusal waited instead of answering"
    finally:
        compile_handler._COMPILE_LOCK.release()

    deadline = time.monotonic() + 10
    while len(waiters) < slots and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [r.status_code for r in waiters] == [200] * slots
    # Every slot is handed back.
    assert compile_handler._COMPILE_SLOTS._value == slots


def test_a_refused_or_failed_compile_hands_its_slot_back(client, compile_env, monkeypatch):
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    slots = 1 + compile_handler.MAX_WAITING_COMPILES
    for _ in range(slots + 2):
        assert _post(client, {"source": "void OnTick(){}"}).status_code == 500
    assert compile_handler._COMPILE_SLOTS._value == slots


# ── include_hash follows the tree (review item 4) ────────────────────


def test_include_hash_follows_an_edit_without_a_local_cache(
    client, compile_env, monkeypatch, tmp_path
):
    """With compile_local_cache unset (the default) nothing used to clear the
    cached digest, so after anyone edited a header every compile kept
    reporting the old hash."""
    include = tmp_path / "MQL5" / "Include"
    include.mkdir(parents=True)
    header = include / "Lib.mqh"
    header.write_text("// v1")
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary"),
    )
    first = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    assert _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"] == first

    header.write_text("// v2, edited in the terminal's MQL5 folder")
    second = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    assert second != first


def test_include_hash_follows_a_refresh_made_by_another_process(
    client, compile_env, monkeypatch, tmp_path
):
    """With a shared mirror, another process's refresh never ran this
    process's invalidation, so its cached digest went stale."""
    mirror = tmp_path / "mirror"
    (mirror / "MQL5" / "Include").mkdir(parents=True)
    header = mirror / "MQL5" / "Include" / "Lib.mqh"
    header.write_text("// v1")
    monkeypatch.setattr(compile_handler, "_LOCAL_TOOLCHAIN_RESOLVED", True)
    monkeypatch.setattr(
        compile_handler, "_LOCAL_TOOLCHAIN",
        (compile_env["editor"], str(mirror / "MQL5")),
    )
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(mirror))
    monkeypatch.setattr(compile_handler, "INCLUDE_REFRESH_SECONDS", 3600)
    monkeypatch.setattr(compile_handler, "_INCLUDES_CHECKED_AT", time.monotonic())
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary"),
    )
    first = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]

    # Another process refreshes the shared mirror; this one does not know.
    header.write_text("// v2 copied in by a sibling process")
    second = _post(client, {"source": "void OnTick(){}"}).get_json()["include_hash"]
    assert second != first


# ── ea_version (review item 6) ───────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    ["1.0\n2026-09-29 12:00:00 INFO forged line", "x" * 65, "", 12, ["1.0"], "1.0 beta"],
)
def test_ea_version_must_be_a_short_simple_token(client, compile_env, monkeypatch, bad):
    ran = []
    monkeypatch.setattr(compile_handler.subprocess, "run", lambda *a, **k: ran.append(a))
    resp = _post(client, {"source": "void OnTick(){}", "ea_version": bad})
    assert resp.status_code == 400
    assert "ea_version" in resp.get_json()["log"]
    assert ran == []


def test_a_simple_ea_version_is_logged(client, compile_env, monkeypatch):
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary"),
    )
    lines = []
    monkeypatch.setattr(compile_handler.log, "info", lambda msg, *a: lines.append(msg % a))
    assert _post(client, {"source": "void OnTick(){}", "ea_version": "2.14.0-rc1"}).status_code == 200
    assert any("ea_version=2.14.0-rc1 " in line for line in lines)


# ── What the source itself can reach (review item 10) ────────────────
#
# MetaEditor resolves #include, #resource and #property icon itself. Measured
# on a real MetaEditor (tests/integration/test_compile_reach.py): #include
# accepts absolute paths and `..` walks, #property icon accepts `..`, and
# #resource refuses both. The handler refuses all of them before MetaEditor
# runs, rather than relying on which ones MetaEditor happens to stop.

SHARED = r"C:\Users\Docker\Desktop\Shared"


@pytest.mark.parametrize(
    "source",
    [
        f'#include "{SHARED}\\config\\config.yaml"',
        f"#include <{SHARED}\\config\\config.yaml>",
        '#include "C:/Users/Docker/Desktop/Shared/config/config.yaml"',
        r'#include "..\..\..\config\config.yaml"',
        '#include "../../../config/config.yaml"',
        r"#include <..\..\..\config\config.yaml>",
        r'#include "\\host.lan\Data\config\config.yaml"',
        r'#include "\Windows\win.ini"',
        r'#include ".. \..\config.yaml"',
        r'#include "...\x.mqh"',
        r'#include "Trade\..\..\x.mqh"',
        r'#include "file.mqh:stream"',
        r'#include "CON"',
        r'#include "nul.mqh"',
        f'   #include "{SHARED}\\x.mqh"',
        f'/* leading comment */#include "{SHARED}\\x.mqh"',
        f'#/* inside */include "{SHARED}\\x.mqh"',
        f'# include "{SHARED}\\x.mqh"',
        f'#INCLUDE "{SHARED}\\x.mqh"',
        r'#resource "..\..\secret.txt" as string s',
        f'#resource "{SHARED}\\secret.txt" as string s',
        r'#resource "\\host.lan\Data\secret.txt"',
        r'#resource "\..\..\secret.txt"',
        r'#property icon "..\..\Users\Docker\Desktop\Shared\x.ico"',
        f'#property icon "{SHARED}\\x.ico"',
        r'#property  icon "\..\x.ico"',
        '#define P "C:\\\\x.mqh"\n#include P',
        # The scan must break lines where MetaEditor does, not only on \n.
        f'int x = 1;\r#include "{SHARED}\\x.mqh"\r',
        f'int x = 1;\u2028#include "{SHARED}\\x.mqh"',
        # A continuation or a comment line break joins the directive for the
        # preprocessor while splitting it for a line-based scan.
        f'#inc\\\nlude "{SHARED}\\x.mqh"',
        f'#\\\ninclude "{SHARED}\\x.mqh"',
        f'#/*\n*/include "{SHARED}\\x.mqh"',
        # Form feed and vertical tab are C whitespace.
        f'\f#include "{SHARED}\\x.mqh"',
        f'#\vinclude "{SHARED}\\x.mqh"',
    ],
)
def test_source_cannot_name_a_file_outside_the_sandbox(client, compile_env, monkeypatch, source):
    ran = []
    monkeypatch.setattr(compile_handler.subprocess, "run", lambda *a, **k: ran.append(a))
    resp = _post(client, {"source": source + "\nvoid OnTick(){}\n"})
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["ok"] is False
    assert ran == [], "MetaEditor ran on a refused source"


@pytest.mark.parametrize(
    "source",
    [
        "#include <Trade\\Trade.mqh>",
        "#include <Trade/Trade.mqh>",
        '#include "Helpers.mqh"',
        '#include ".\\Helpers.mqh"',
        '#resource "\\Images\\logo.bmp"',
        '#resource "\\Files\\data.csv" as string data',
        '#resource "logo.bmp"',
        '#property icon "\\Images\\app.ico"',
        '#property copyright "C:\\\\ is not a path here"',
        '#import "kernel32.dll"\nint GetTickCount();\n#import',
        f'// #include "{SHARED}\\x.mqh" in a comment is not a directive',
        f'/*\n#include "{SHARED}\\x.mqh"\n*/',
        'string s = "#include \\"C:\\\\x.mqh\\"";',
        'Print("// not a comment"); #include <Trade\\Trade.mqh>',
        "#define SUM(a, b) \\\n    ((a) + (b))\n#include <Trade\\Trade.mqh>",
        "#include <Trade\\Trade.mqh>\r\nint x = 1;\r\n",
    ],
)
def test_ordinary_directives_still_compile(client, compile_env, monkeypatch, source):
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary"),
    )
    resp = _post(client, {"source": source + "\nvoid OnTick(){}\n"})
    assert resp.status_code == 200, resp.get_json()


def test_metaeditor_reads_the_line_breaks_the_scan_checked(client, compile_env, monkeypatch):
    """The written file must carry only \\r\\n breaks. A lone \\r left in it could
    start a line for MetaEditor that the scan never saw as one."""
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    written = []
    fake = _fake_metaeditor(_utf16_log(SUCCESS_LOG), ex5_bytes=b"binary")

    def _capture_then_compile(cmd, **kwargs):
        src_path = next(a.split(":", 1)[1] for a in cmd if a.startswith("/compile:"))
        with open(src_path, "rb") as fh:
            written.append(fh.read())
        return fake(cmd, **kwargs)

    monkeypatch.setattr(compile_handler.subprocess, "run", _capture_then_compile)

    resp = _post(client, {"source": "int a = 1;\rint b = 2;\r\nint c = 3;\nvoid OnTick(){}\n"})

    assert resp.status_code == 200, resp.get_json()
    body = written[0].replace(b"\r\n", b"")
    assert b"\r" not in body
    assert written[0].count(b"\r\n") == 4


def test_the_refusal_names_the_directive_and_path(client, compile_env):
    resp = _post(client, {"source": '#include "..\\secret.mqh"\n'})
    body = resp.get_json()
    assert resp.status_code == 400
    assert '#include "..\\secret.mqh"' in body["log"]
    assert "parent-directory" in body["log"]


# ── compile_timeout and the /compile byte caps ───────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [("10", 10), ("45", 45), ("45s", 45), ("1m", 60), ("1", 1)],
)
def test_compile_timeout_reads_bare_numbers_as_seconds(monkeypatch, raw, expected):
    """A bare number used to be read as hours: 10 became 36000s, clamped to
    60."""
    monkeypatch.setenv("COMPILE_TIMEOUT", raw)

    assert config._compile_timeout() == expected


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "0s", "1.5.2"])
def test_an_invalid_compile_timeout_falls_back_to_the_default_with_a_warning(
    monkeypatch, caplog, raw
):
    """'abc' used to raise while config.py was imported, so the whole API -
    trading included - failed to start over one optional compile setting."""
    monkeypatch.setenv("COMPILE_TIMEOUT", raw)

    with caplog.at_level("WARNING", logger="mt5api.config"):
        assert config._compile_timeout() == config.COMPILE_TIMEOUT_DEFAULT_SECONDS
    assert "compile_timeout" in caplog.text


def test_a_compile_timeout_above_the_ceiling_is_lowered_with_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("COMPILE_TIMEOUT", "5m")

    with caplog.at_level("WARNING", logger="mt5api.config"):
        assert config._compile_timeout() == config.COMPILE_TIMEOUT_CEILING_SECONDS
    assert "ceiling" in caplog.text


@pytest.mark.parametrize("name", ["COMPILE_MAX_SOURCE_BYTES", "COMPILE_MAX_EX5_BYTES"])
@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_an_invalid_compile_byte_cap_falls_back_to_the_default_with_a_warning(
    monkeypatch, caplog, name, bad
):
    """0 or below used to become 1024 bytes, which refuses almost every real
    EA, and said nothing."""
    monkeypatch.setenv(name, bad)

    with caplog.at_level("WARNING", logger="mt5api.config"):
        assert config._positive_int_setting(name, name.lower(), 2 * 1024 * 1024) == 2 * 1024 * 1024
    assert name in caplog.text


# ── MQL4: a `.mq4` filename selects the MT4 MetaEditor on the same route ─────

MT4_SUCCESS_LOG = (
    "ea.mq4 : information: compiling 'ea.mq4'\n"
    "Result: 0 errors, 0 warnings, 87 msec elapsed\n"
)


@pytest.fixture
def mt4_env(monkeypatch, tmp_path):
    """An MT4 install that 'exists', and none of MT5's mirror state."""
    mt4 = tmp_path / "mt4"
    (mt4 / "MQL4" / "Include").mkdir(parents=True)
    (mt4 / "MQL4" / "Include" / "stdlib.mqh").write_text("// stdlib")
    (mt4 / "metaeditor.exe").write_bytes(b"MZ")
    monkeypatch.setattr(compile_handler, "COMPILE_MT4_TERMINAL_DIR", str(mt4))
    monkeypatch.setattr(compile_handler, "COMPILE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(compile_handler, "COMPILE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(compile_handler, "_MT4_TOOLCHAIN", None)
    monkeypatch.setattr(compile_handler, "_MT4_TOOLCHAIN_RESOLVED", False)
    return mt4


def _fake_mt4_editor(log_bytes, ex4_bytes=None, record=None):
    def _run(cmd, **kwargs):
        if record is not None:
            record.append(cmd)
        log_path = next(a.split(":", 1)[1] for a in cmd if a.startswith("/log:"))
        src_path = next(a.split(":", 1)[1] for a in cmd if a.startswith("/compile:"))
        with open(log_path, "wb") as fh:
            fh.write(log_bytes)
        if ex4_bytes is not None:
            with open(os.path.splitext(src_path)[0] + ".ex4", "wb") as fh:
                fh.write(ex4_bytes)
        return FakeCompleted(0)
    return _run


@pytest.mark.parametrize("supplied,expected", [
    ("ea.mq4", "ea"),
    ("MyEA.MQ4", "MyEA"),
    ("..\\..\\evil.mq4", "evil"),
])
def test_mq4_filename_is_reduced_to_a_harmless_stem(supplied, expected):
    assert compile_handler._safe_stem(supplied) == expected


@pytest.mark.parametrize("filename,expected", [
    ("ea.mq4", "mt4"), ("EA.MQ4", "mt4"), (" ea.mq4 ", "mt4"),
    ("ea.mq5", "mt5"), ("ea", "mt5"), ("", "mt5"), (None, "mt5"),
    ("ea.mq4.mq5", "mt5"),
])
def test_platform_is_chosen_by_the_filename_extension(filename, expected):
    assert compile_handler._platform_for(filename) == expected


def test_mq4_compiles_with_the_mt4_editor_and_returns_an_ex4(
    client, compile_env, mt4_env, monkeypatch
):
    recorded = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_mt4_editor(_utf16_log(MT4_SUCCESS_LOG), b"ex4-bytes", record=recorded),
    )
    resp = _post(client, {"source": "void start(){}", "filename": "ea.mq4"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert base64.b64decode(body["ex4_base64"]) == b"ex4-bytes"
    assert "ex5_base64" not in body
    assert body["warnings"] == 0
    # MetaEditor 4, the MT4 include tree, and a .mq4 source - never MT5's editor.
    assert recorded[0][0] == str(mt4_env / "metaeditor.exe")
    assert next(a for a in recorded[0] if a.startswith("/compile:")).endswith(".mq4")
    assert next(a for a in recorded[0] if a.startswith("/inc:")) == f"/inc:{mt4_env / 'MQL4'}"


def test_mq5_requests_are_unchanged_when_mt4_is_installed(
    client, compile_env, mt4_env, monkeypatch
):
    recorded = []
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5", record=recorded),
    )
    body = _post(client, {"source": "void OnTick(){}", "filename": "ea.mq5"}).get_json()
    assert base64.b64decode(body["ex5_base64"]) == b"ex5"
    assert "ex4_base64" not in body
    assert recorded[0][0] == compile_env["editor"]


def test_mq4_errors_take_the_same_422_path(client, compile_env, mt4_env, monkeypatch):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_mt4_editor(_utf16_log(ERROR_LOG)),
    )
    resp = _post(client, {"source": "bad", "filename": "ea.mq4"})
    assert resp.status_code == 422
    assert resp.get_json()["errors"] == 3


def test_clean_mq4_log_without_an_ex4_is_never_success(
    client, compile_env, mt4_env, monkeypatch
):
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_mt4_editor(_utf16_log(MT4_SUCCESS_LOG)),
    )
    resp = _post(client, {"source": "void start(){}", "filename": "ea.mq4"})
    assert resp.status_code == 422
    assert "no .ex4" in resp.get_json()["log"]


def test_mq4_without_an_mt4_install_is_a_json_500_and_mq5_still_works(
    client, compile_env, mt4_env, monkeypatch
):
    (mt4_env / "metaeditor.exe").unlink()
    resp = _post(client, {"source": "void start(){}", "filename": "ea.mq4"})
    assert resp.status_code == 500
    assert resp.get_json()["log"] == "MT4 MetaEditor is not available on this host"

    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", "")
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_metaeditor(_utf16_log(SUCCESS_LOG), b"ex5"),
    )
    assert _post(client, {"source": "void OnTick(){}"}).status_code == 200


def test_metaeditor64_is_preferred_over_the_32_bit_editor(mt4_env):
    (mt4_env / "metaeditor64.exe").write_bytes(b"MZ")
    assert compile_handler._mt4_editor(str(mt4_env)) == str(mt4_env / "metaeditor64.exe")


def test_mt4_mirror_lives_under_its_own_subdirectory_and_is_built_once(
    client, compile_env, mt4_env, monkeypatch, tmp_path
):
    cache = tmp_path / "cache"
    monkeypatch.setattr(compile_handler, "COMPILE_LOCAL_CACHE", str(cache))
    recorded = []
    monkeypatch.setattr(
        compile_handler.subprocess, "run",
        _fake_mt4_editor(_utf16_log(MT4_SUCCESS_LOG), b"ex4", record=recorded),
    )
    copies = []
    real_copy = compile_handler.shutil.copy2
    monkeypatch.setattr(
        compile_handler.shutil, "copy2",
        lambda *a, **k: (copies.append(a[0]), real_copy(*a, **k))[1],
    )
    for _ in range(3):
        assert _post(client, {"source": "x", "filename": "ea.mq4"}).status_code == 200

    assert recorded[0][0] == str(cache / "mt4" / "metaeditor.exe")
    assert (cache / "mt4" / "MQL4" / "Include" / "stdlib.mqh").exists()
    # Not in the MT5 mirror's root, so the two cannot collide.
    assert not (cache / "MQL4").exists()
    assert len(copies) == 2, f"expected editor + one header, copied {copies}"
