"""Tests for scripts/verify_binaries.py — the vendored-binary gate.

This gate is the only thing standing between the repo and an unreadable blob
riding in on an otherwise-legitimate PR, so its failure modes matter more than
its happy path. Every test here breaks something on purpose and asserts the
gate notices; a gate that cannot fail is worse than no gate, because it buys
confidence it has not earned.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "verify_binaries.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_binaries", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


verify_binaries = _load_module()


def _minimal_pe(cert_revision=0x0200, cert_type=0x0002, with_cert=True):
    """Build the smallest PE-shaped blob the parser will walk.

    Hand-rolled rather than fixtured from a real executable: a test that needs
    a 1 MB binary checked in to run is a test nobody runs, and shipping more
    opaque binaries to test the anti-opaque-binary gate would be its own joke.
    """
    pe_offset = 0x80
    data = bytearray(b"\x00" * 0x400)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = struct.pack("<I", pe_offset)
    data[pe_offset:pe_offset + 4] = b"PE\x00\x00"

    optional = pe_offset + 24
    data[optional:optional + 2] = struct.pack("<H", 0x20B)  # PE32+

    directories = optional + 112
    security = directories + 4 * 8
    cert_offset = 0x300 if with_cert else 0
    cert_size = 0x40 if with_cert else 0
    data[security:security + 4] = struct.pack("<I", cert_offset)
    data[security + 4:security + 8] = struct.pack("<I", cert_size)

    if with_cert:
        data[cert_offset:cert_offset + 8] = struct.pack(
            "<IHH", cert_size, cert_revision, cert_type
        )

    return bytes(data)


def test_classify_detects_pe_and_ignores_text(tmp_path):
    exe = tmp_path / "thing.exe"
    exe.write_bytes(_minimal_pe())
    text = tmp_path / "readme.md"
    text.write_text("not a binary", encoding="utf-8")

    assert verify_binaries.classify(str(exe)) == "pe"
    assert verify_binaries.classify(str(text)) is None


@pytest.mark.parametrize("magic,kind", [
    (b"\x7fELF\x00\x00", "elf"),
    (b"\xfe\xed\xfa\xcf\x00", "macho"),
    (b"\xca\xfe\xba\xbe\x00", "macho-fat"),
])
def test_classify_detects_non_windows_executables(tmp_path, magic, kind):
    # A repo that builds for Windows from Linux CI can still receive an ELF or
    # a Mach-O; catching only .exe would leave the obvious hole open.
    target = tmp_path / "artifact"
    target.write_bytes(magic + b"\x00" * 64)

    assert verify_binaries.classify(str(target)) == kind


def test_signature_state_flags_malformed_win_certificate(tmp_path):
    # This is the shape the vendored PowerRun.exe actually has: a certificate
    # directory pointing at something that is not a WIN_CERTIFICATE.
    exe = tmp_path / "repacked.exe"
    exe.write_bytes(_minimal_pe(cert_revision=0xC496, cert_type=14951))

    state, detail = verify_binaries.pe_signature_state(str(exe))

    assert state == verify_binaries.SIG_MALFORMED
    assert "WIN_CERTIFICATE" in detail


def test_signature_state_reports_absent_when_unsigned(tmp_path):
    exe = tmp_path / "unsigned.exe"
    exe.write_bytes(_minimal_pe(with_cert=False))

    state, _ = verify_binaries.pe_signature_state(str(exe))

    assert state == verify_binaries.SIG_ABSENT


def test_signature_state_confirms_a_matching_authenticode_digest(tmp_path):
    """A file whose embedded digest matches its own bytes reads as valid.

    Built by computing the Authenticode digest over the finished layout and
    writing it into the certificate blob — the same relationship a real signer
    creates, without needing a signing key.
    """
    base = bytearray(_minimal_pe())
    checksum_off, security_off, cert_off, cert_size = (
        verify_binaries._security_directory(bytes(base))
    )

    digest = hashlib.sha256()
    digest.update(bytes(base[:checksum_off]))
    digest.update(bytes(base[checksum_off + 4:security_off]))
    digest.update(bytes(base[security_off + 8:cert_off]))
    base[cert_off + 8:cert_off + 40] = digest.digest()

    exe = tmp_path / "signed.exe"
    exe.write_bytes(bytes(base))

    state, detail = verify_binaries.pe_signature_state(str(exe))

    assert state == verify_binaries.SIG_VALID
    assert "authenticode" in detail


def _run_gate():
    return subprocess.run(
        [sys.executable, SCRIPT], capture_output=True, text=True, cwd=REPO_ROOT
    )


def _repo_is_complete() -> bool:
    """True only in a full checkout.

    The test image COPYs a subset of the tree, so the declared binaries are not
    present there and a repo-wide run would report them missing. The gate runs
    for real against the full checkout via `make verify-binaries`; here we skip
    rather than assert something false about a partial tree.
    """
    with open(verify_binaries.MANIFEST_PATH, encoding="utf-8") as handle:
        manifest = json.load(handle)

    return all(
        os.path.exists(os.path.join(REPO_ROOT, entry["path"]))
        for entry in manifest["binaries"]
    )


needs_full_repo = pytest.mark.skipif(
    not _repo_is_complete(),
    reason="partial checkout (test image); full gate runs via make verify-binaries",
)


@needs_full_repo
def test_gate_passes_on_the_repo_as_committed():
    result = _run_gate()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "verdict: OK" in result.stdout


@needs_full_repo
def test_gate_fails_on_an_undeclared_binary(tmp_path):
    """The headline case: a binary lands that nobody declared.

    Uses git's index so the file is tracked without a commit, mirroring how an
    undeclared binary would actually arrive in a PR.
    """
    planted = os.path.join(REPO_ROOT, "assets", "_gate_probe.exe")
    try:
        with open(planted, "wb") as handle:
            handle.write(_minimal_pe())
        subprocess.run(
            ["git", "-C", REPO_ROOT, "add", "-N", planted], check=True
        )

        result = _run_gate()

        assert result.returncode == 1
        assert "UNDECLARED" in result.stdout
    finally:
        subprocess.run(
            ["git", "-C", REPO_ROOT, "rm", "-q", "--cached", "--force", planted],
            check=False,
        )
        if os.path.exists(planted):
            os.remove(planted)


def test_manifest_entries_are_complete():
    """Every declared binary carries the provenance a reader needs.

    A manifest entry without a source URL is a hash with no story — it proves
    the bytes did not change but says nothing about whether they were right to
    begin with.
    """
    with open(verify_binaries.MANIFEST_PATH, encoding="utf-8") as handle:
        manifest = json.load(handle)

    assert manifest["binaries"], "manifest must not be empty while binaries exist"

    for entry in manifest["binaries"]:
        for field in ("path", "sha256", "size", "source", "signature"):
            assert entry.get(field), f"{entry.get('path')} missing {field}"

        assert len(entry["sha256"]) == 64, f"{entry['path']}: not a sha256"

        if entry["signature"] in (
            verify_binaries.SIG_MALFORMED,
            verify_binaries.SIG_ABSENT,
        ):
            assert entry.get("note"), (
                f"{entry['path']}: an unverifiable binary must carry a note "
                f"explaining why it is tolerated"
            )
