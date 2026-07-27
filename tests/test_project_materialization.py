import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from mt5api.backtest.materialization import (
    MaterializationError,
    ProjectMaterialization,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_project_contract(root: Path, *, omit_bundle_entry=None):
    payloads = {
        "payload/Expert.ex5": b"compiled-expert-real-bytes\x00\x01",
        "payload/Expert.set": b"InpVolume=0.08||0.08||0.01||1.00||N\r\n",
        "payload/input.csv": b"event,value\r\nbaseline,1\r\n",
    }
    artifacts = [
        {
            "module": "EXP",
            "relativePath": "EXP-Contract/Expert.ex5",
            "bundleEntry": "payload/Expert.ex5",
        },
        {
            "module": "PRS",
            "relativePath": "PRS-Contract/Expert.set",
            "bundleEntry": "payload/Expert.set",
        },
        {
            "module": "FLS",
            "relativePath": "FLS-Contract/input.csv",
            "bundleEntry": "payload/input.csv",
        },
    ]
    for artifact in artifacts:
        payload = payloads[artifact["bundleEntry"]]
        artifact["size"] = len(payload)
        artifact["sha256"] = _sha256(payload)

    manifest = {
        "schemaVersion": "MT5-CLI-PROJECT-MATERIALIZATION-001",
        "projectId": "project-real-contract",
        "runId": "run-real-contract",
        "planFingerprint": "plan-real-contract",
        "expertRelativePath": "EXP-Contract/Expert.ex5",
        "presetRelativePath": "PRS-Contract/Expert.set",
        "artifacts": artifacts,
    }
    manifest_path = root / "project-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    bundle_path = root / "project-bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry, payload in payloads.items():
            if entry != omit_bundle_entry:
                archive.writestr(entry, payload)
    return manifest_path, bundle_path, payloads


def test_materialization_applies_captures_and_restores_real_bytes(tmp_path):
    terminal_dir = tmp_path / "terminal"
    common_files_dir = tmp_path / "common-files"
    stage_dir = tmp_path / "job"
    old_expert = terminal_dir / "MQL5" / "Experts" / "EXP-Contract" / "old.ex5"
    old_common = common_files_dir / "FLS-Contract" / "preexisting.csv"
    old_expert.parent.mkdir(parents=True)
    old_common.parent.mkdir(parents=True)
    old_expert.write_bytes(b"previous-expert-byte-exact")
    old_common.write_bytes(b"previous,common,bytes\r\n")
    manifest_path, bundle_path, payloads = _write_project_contract(tmp_path)

    materialization = ProjectMaterialization(
        str(manifest_path),
        str(bundle_path),
        str(stage_dir),
        str(terminal_dir),
        str(common_files_dir),
    )
    materialization.apply()

    applied_expert = terminal_dir / "MQL5" / "Experts" / "EXP-Contract" / "Expert.ex5"
    applied_input = common_files_dir / "FLS-Contract" / "input.csv"
    assert applied_expert.read_bytes() == payloads["payload/Expert.ex5"]
    assert applied_input.read_bytes() == payloads["payload/input.csv"]
    assert not old_expert.exists()
    assert not old_common.exists()

    applied_input.write_bytes(b"event,value\r\nbaseline,2\r\n")
    generated_output = common_files_dir / "FLS-Contract" / "trades.csv"
    generated_output_payload = b"ticket,profit\r\n1001,12.34\r\n"
    generated_output.write_bytes(generated_output_payload)
    output_manifest = materialization.capture_outputs()

    classifications = {
        item["relativePath"]: item["classification"]
        for item in output_manifest["files"]
    }
    assert classifications == {
        "FLS-Contract/input.csv": "modified",
        "FLS-Contract/trades.csv": "created",
    }
    output_archive_path = Path(materialization.output_archive_path)
    assert output_manifest["archiveSha256"] == _sha256(output_archive_path.read_bytes())
    with zipfile.ZipFile(output_archive_path, "r") as archive:
        assert sorted(archive.namelist()) == [
            "FLS/FLS-Contract/input.csv",
            "FLS/FLS-Contract/trades.csv",
        ]
        assert archive.read("FLS/FLS-Contract/trades.csv") == generated_output_payload

    materialization.restore()
    assert old_expert.read_bytes() == b"previous-expert-byte-exact"
    assert old_common.read_bytes() == b"previous,common,bytes\r\n"
    assert not applied_expert.exists()
    assert not applied_input.exists()
    assert not generated_output.exists()
    audit = json.loads(Path(materialization.audit_path).read_text(encoding="utf-8"))
    assert audit["status"] == "restored"
    assert audit["modules"]["EXP"]["restoredSnapshot"]["old.ex5"]["sha256"] == _sha256(
        b"previous-expert-byte-exact"
    )


def test_materialization_rejects_bundle_missing_declared_real_file(tmp_path):
    manifest_path, bundle_path, _ = _write_project_contract(
        tmp_path,
        omit_bundle_entry="payload/Expert.set",
    )

    with pytest.raises(MaterializationError) as failure:
        ProjectMaterialization(
            str(manifest_path),
            str(bundle_path),
            str(tmp_path / "job"),
            str(tmp_path / "terminal"),
            str(tmp_path / "common-files"),
        )

    assert failure.value.code == "bundle_manifest_mismatch"
    assert failure.value.details == {
        "missing": ["payload/expert.set"],
        "unexpected": [],
    }
