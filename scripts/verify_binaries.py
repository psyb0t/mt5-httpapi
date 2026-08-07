#!/usr/bin/env python3
"""Fail the build when a binary in this repo is not what it claims to be.

WHY THIS EXISTS
---------------
A repo that vendors executables has exactly one hard problem: nobody can read
them. A reviewer skims 5,000 lines of Python and waves through the 1 MB `.exe`
sitting next to it, because there is nothing to skim. That is the shape every
serious supply-chain compromise has taken -- a long-trusted contributor, a
mountain of legitimate work, and one opaque blob nobody diffed.

So every binary tracked in this repo must be declared in `assets/binaries.lock.json`
with its SHA-256, its upstream, and the state of its code signature. This script
walks the tracked tree and refuses anything that does not line up:

  * an executable nobody declared                  -> FAIL (the important one)
  * a declared binary whose bytes changed          -> FAIL
  * a signature that was valid and now is not      -> FAIL
  * a signature that was already broken            -> WARN, loudly, every run

The last case is deliberate. `scripts/defender-remover/PowerRun.exe` arrived
repacked -- its certificate header is structurally invalid, and its hash matches
no upstream release -- and pretending otherwise would either block the build
forever or hide the fact. Recording it makes it visible on every single run
without holding the repo hostage, and any CHANGE to it still fails hard.

Run it: `make verify-binaries`, or as part of `make lint`.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(REPO_ROOT, "assets", "binaries.lock.json")

# Magic numbers for the executable formats worth catching. A repo targeting
# Windows VMs from Linux CI can plausibly receive any of them.
EXECUTABLE_MAGICS = (
    (b"MZ", "pe"),          # Windows PE (.exe/.dll)
    (b"\x7fELF", "elf"),    # Linux
    (b"\xfe\xed\xfa\xce", "macho"),
    (b"\xfe\xed\xfa\xcf", "macho"),
    (b"\xcf\xfa\xed\xfe", "macho"),
    (b"\xca\xfe\xba\xbe", "macho-fat"),
)

# Signature states a manifest entry may declare.
SIG_VALID = "valid"          # Authenticode digest matches the file's bytes
SIG_MALFORMED = "malformed"  # certificate directory is not a WIN_CERTIFICATE
SIG_ABSENT = "absent"        # no signature at all
SIG_UNCHECKED = "unchecked"  # not a PE, or verification not applicable

WIN_CERT_REVISION_2_0 = 0x0200
WIN_CERT_TYPE_PKCS_SIGNED_DATA = 0x0002

PE_SIGNATURE_OFFSET_POINTER = 0x3C
OPTIONAL_HEADER_OFFSET = 24
PE32_PLUS_MAGIC = 0x20B
CHECKSUM_OFFSET_IN_OPTIONAL_HEADER = 64
DATA_DIRECTORY_OFFSET_PE32 = 96
DATA_DIRECTORY_OFFSET_PE32_PLUS = 112
SECURITY_DIRECTORY_INDEX = 4
DATA_DIRECTORY_ENTRY_SIZE = 8
WIN_CERTIFICATE_HEADER_SIZE = 8


def tracked_files() -> list[str]:
    """Every file git tracks, so untracked scratch never trips the gate.

    Falls back to a filesystem walk where git is unavailable — the test image
    COPYs sources without `.git`, and a gate that silently reports "0 binaries
    checked" because git was missing would be worse than one that errors.
    """
    try:
        out = subprocess.run(
            ["git", "-C", REPO_ROOT, "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
        return [p for p in out.stdout.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        pass

    skip = {".git", "node_modules", "__pycache__", ".venv-test", ".pytest_cache"}
    found: list[str] = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            found.append(
                os.path.relpath(os.path.join(root, name), REPO_ROOT)
            )

    return found


def classify(path: str) -> str | None:
    """Return the executable kind for `path`, or None if it is not one."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError:
        return None

    for magic, kind in EXECUTABLE_MAGICS:
        if head.startswith(magic):
            return kind

    return None


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)

    return digest.hexdigest()


def _security_directory(data: bytes) -> tuple[int, int, int, int]:
    """Locate the PE checksum field and the certificate table.

    Returns (checksum_offset, security_dir_offset, cert_offset, cert_size).
    """
    pe_offset = int.from_bytes(
        data[PE_SIGNATURE_OFFSET_POINTER:PE_SIGNATURE_OFFSET_POINTER + 4],
        "little",
    )
    optional = pe_offset + OPTIONAL_HEADER_OFFSET
    magic = int.from_bytes(data[optional:optional + 2], "little")
    directories = optional + (
        DATA_DIRECTORY_OFFSET_PE32_PLUS
        if magic == PE32_PLUS_MAGIC
        else DATA_DIRECTORY_OFFSET_PE32
    )
    security = directories + SECURITY_DIRECTORY_INDEX * DATA_DIRECTORY_ENTRY_SIZE
    cert_offset = int.from_bytes(data[security:security + 4], "little")
    cert_size = int.from_bytes(data[security + 4:security + 8], "little")

    return (
        optional + CHECKSUM_OFFSET_IN_OPTIONAL_HEADER,
        security,
        cert_offset,
        cert_size,
    )


def pe_signature_state(path: str) -> tuple[str, str]:
    """Classify a PE file's Authenticode signature.

    Returns (state, detail). The Authenticode digest covers the whole file
    EXCEPT three regions the signing process itself writes: the optional
    header's checksum field, the certificate table's directory entry, and the
    certificate blob. Recomputing it and finding it inside the PKCS#7 proves the
    bytes have not moved since signing.

    This is deliberately a structural + digest check, not a full chain
    validation: verifying the certificate chain needs a trust store this
    container does not have, and a valid chain is not what protects us here.
    What protects us is noticing CHANGE, which the manifest's pinned hash does
    unconditionally.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return SIG_UNCHECKED, f"unreadable: {exc}"

    try:
        checksum_off, security_off, cert_off, cert_size = _security_directory(data)
    except (IndexError, struct.error) as exc:
        return SIG_MALFORMED, f"PE headers unparseable: {exc}"

    if not cert_size or cert_off <= 0 or cert_off >= len(data):
        return SIG_ABSENT, "no certificate table"

    header = data[cert_off:cert_off + WIN_CERTIFICATE_HEADER_SIZE]
    if len(header) < WIN_CERTIFICATE_HEADER_SIZE:
        return SIG_MALFORMED, "certificate table truncated"

    declared_len, revision, cert_type = struct.unpack("<IHH", header)
    if revision != WIN_CERT_REVISION_2_0 or cert_type != WIN_CERT_TYPE_PKCS_SIGNED_DATA:
        return (
            SIG_MALFORMED,
            f"bad WIN_CERTIFICATE header: len={declared_len} "
            f"revision={hex(revision)} type={cert_type} "
            f"(expected revision=0x200 type=2)",
        )

    if declared_len > cert_size:
        return (
            SIG_MALFORMED,
            f"declared certificate length {declared_len} exceeds "
            f"directory size {cert_size}",
        )

    digest = hashlib.sha256()
    digest.update(data[:checksum_off])
    digest.update(data[checksum_off + 4:security_off])
    digest.update(data[security_off + DATA_DIRECTORY_ENTRY_SIZE:cert_off])
    computed = digest.hexdigest()

    blob = data[cert_off:cert_off + cert_size]
    if bytes.fromhex(computed) in blob:
        return SIG_VALID, f"authenticode sha256 {computed[:16]}… matches"

    # A well-formed signature whose digest we cannot locate is NOT proof of
    # tampering: signers may use SHA-1, and the digest sits inside DER we do
    # not fully parse. Say exactly that rather than implying a verdict.
    return (
        SIG_UNCHECKED,
        "well-formed signature; sha256 digest not located "
        "(may be SHA-1 signed — pinned hash is the real guard)",
    )


def load_manifest() -> dict[str, dict]:
    if not os.path.exists(MANIFEST_PATH):
        print(f"FAIL: manifest missing: {MANIFEST_PATH}")
        raise SystemExit(1)

    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        raw = json.load(handle)

    return {entry["path"]: entry for entry in raw.get("binaries", [])}


def main() -> int:
    manifest = load_manifest()
    failures: list[str] = []
    warnings: list[str] = []
    checked = 0

    found: set[str] = set()
    for rel in tracked_files():
        absolute = os.path.join(REPO_ROOT, rel)
        kind = classify(absolute)
        if kind is None:
            continue

        found.add(rel)
        checked += 1
        entry = manifest.get(rel)

        if entry is None:
            failures.append(
                f"{rel}: UNDECLARED {kind} binary — add it to "
                f"assets/binaries.lock.json with its upstream and sha256, "
                f"or remove it from the repo"
            )
            continue

        actual_hash = sha256_of(absolute)
        actual_size = os.path.getsize(absolute)

        if actual_hash != entry.get("sha256"):
            failures.append(
                f"{rel}: SHA-256 CHANGED\n"
                f"      declared {entry.get('sha256')}\n"
                f"      actual   {actual_hash}"
            )
            continue

        if actual_size != entry.get("size"):
            failures.append(
                f"{rel}: size changed — declared {entry.get('size')}, "
                f"actual {actual_size}"
            )
            continue

        state, detail = (
            pe_signature_state(absolute) if kind == "pe" else (SIG_UNCHECKED, kind)
        )
        expected = entry.get("signature", SIG_UNCHECKED)

        if state != expected:
            # Only a DOWNGRADE is a failure. Becoming valid is good news that
            # still needs the manifest updated, so report it as a failure the
            # author must acknowledge — silently accepting it would let the
            # field rot into meaninglessness.
            failures.append(
                f"{rel}: signature state is '{state}', manifest says "
                f"'{expected}' — {detail}"
            )
            continue

        if state in (SIG_MALFORMED, SIG_ABSENT):
            warnings.append(
                f"{rel}: signature {state} (known) — {entry.get('note', '')}"
            )

    for rel in sorted(set(manifest) - found):
        failures.append(
            f"{rel}: declared in the manifest but not tracked in the repo — "
            f"delete the entry if the binary is gone"
        )

    print(f"binary verification: {checked} tracked executable(s) checked")
    for line in warnings:
        print(f"  WARN  {line}")
    for line in failures:
        print(f"  FAIL  {line}")

    if failures:
        print(f"\nverdict: FAILED ({len(failures)} problem(s))")
        return 1

    print(f"verdict: OK ({len(warnings)} known-unsigned, 0 problems)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
