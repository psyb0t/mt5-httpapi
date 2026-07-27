"""Byte-exact project materialization for mt5-cli backtest jobs.

This module deliberately does not infer missing project artifacts.  A caller
must provide a complete manifest plus a ZIP containing every listed byte.  The
module verifies the manifest, stages every payload, replaces only the declared
project module directories while the caller holds the terminal run lock, then
restores the prior directories byte for byte.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath


SCHEMA_VERSION = "MT5-CLI-PROJECT-MATERIALIZATION-001"
OUTPUT_SCHEMA_VERSION = "MT5-CLI-PROJECT-OUTPUTS-001"
AUDIT_SCHEMA_VERSION = "MT5-CLI-PROJECT-MATERIALIZATION-AUDIT-001"

MODULE_ROOT_PARTS = {
    "EXP": ("MQL5", "Experts"),
    "IND": ("MQL5", "Indicators"),
    "SCR": ("MQL5", "Scripts"),
    "SRV": ("MQL5", "Services"),
    "INC": ("MQL5", "Include"),
    "LBR": ("MQL5", "Libraries"),
    "PRS": ("MQL5", "Presets"),
    "TPL": ("Profiles", "Templates"),
}
MODULES = frozenset((*MODULE_ROOT_PARTS.keys(), "FLS"))
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class MaterializationError(ValueError):
    """A factual validation or filesystem failure with an explicit code."""

    def __init__(self, code: str, message: str, *, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict:
        payload = {"code": self.code, "message": str(self)}
        if self.details is not None:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class Artifact:
    module: str
    relative_path: str
    bundle_entry: str
    size: int
    sha256: str

    @property
    def parts(self):
        return PurePosixPath(self.relative_path).parts

    @property
    def module_directory(self) -> str:
        return self.parts[0]

    @property
    def path_inside_module(self) -> str:
        return PurePosixPath(*self.parts[1:]).as_posix()


@dataclass(frozen=True)
class ProjectManifest:
    raw: dict
    project_id: str
    run_id: str
    plan_fingerprint: str
    expert_relative_path: str
    preset_relative_path: str | None
    artifacts: tuple[Artifact, ...]

    @property
    def module_directories(self) -> dict[str, str]:
        result = {}
        for artifact in self.artifacts:
            previous = result.setdefault(artifact.module, artifact.module_directory)
            if previous != artifact.module_directory:
                raise MaterializationError(
                    "multiple_module_directories",
                    f"Module {artifact.module} contains more than one project directory",
                    details={
                        "module": artifact.module,
                        "first": previous,
                        "second": artifact.module_directory,
                    },
                )
        return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: str, value: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def _require_identity(value, field: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise MaterializationError(
            "invalid_identity",
            f"{field} must match {_IDENTITY_RE.pattern}",
            details={"field": field, "value": value},
        )
    return value


def _safe_relative_path(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterializationError(
            "invalid_relative_path",
            f"{field} must be a non-empty POSIX relative path",
            details={"field": field, "value": value},
        )
    if "\\" in value:
        raise MaterializationError(
            "non_canonical_relative_path",
            f"{field} must use forward slashes",
            details={"field": field, "value": value},
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise MaterializationError(
            "unsafe_relative_path",
            f"{field} is not a safe relative path",
            details={"field": field, "value": value},
        )
    canonical = path.as_posix()
    if canonical != value:
        raise MaterializationError(
            "non_canonical_relative_path",
            f"{field} is not canonical",
            details={"field": field, "value": value, "canonical": canonical},
        )
    return canonical


def _parse_artifact(raw, index: int) -> Artifact:
    if not isinstance(raw, dict):
        raise MaterializationError(
            "invalid_artifact",
            f"artifacts[{index}] must be an object",
            details={"index": index},
        )
    module = raw.get("module")
    if module not in MODULES:
        raise MaterializationError(
            "invalid_module",
            f"artifacts[{index}].module is not supported",
            details={"index": index, "module": module, "supported": sorted(MODULES)},
        )
    relative_path = _safe_relative_path(
        raw.get("relativePath"), f"artifacts[{index}].relativePath"
    )
    bundle_entry = _safe_relative_path(
        raw.get("bundleEntry"), f"artifacts[{index}].bundleEntry"
    )
    parts = PurePosixPath(relative_path).parts
    if len(parts) < 2 or not parts[0].startswith(f"{module}-"):
        raise MaterializationError(
            "module_directory_mismatch",
            f"artifacts[{index}].relativePath must begin with {module}-<project>/",
            details={"index": index, "module": module, "relativePath": relative_path},
        )
    size = raw.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise MaterializationError(
            "invalid_artifact_size",
            f"artifacts[{index}].size must be a non-negative integer",
            details={"index": index, "size": size},
        )
    sha256 = raw.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise MaterializationError(
            "invalid_artifact_sha256",
            f"artifacts[{index}].sha256 must contain 64 hexadecimal characters",
            details={"index": index, "sha256": sha256},
        )
    return Artifact(module, relative_path, bundle_entry, size, sha256.lower())


def load_project_manifest(path: str) -> ProjectManifest:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(
            "manifest_unreadable",
            f"Project manifest cannot be read: {exc}",
            details={"path": path, "errorType": type(exc).__name__},
        ) from exc
    if not isinstance(raw, dict):
        raise MaterializationError("manifest_not_object", "Project manifest must be a JSON object")
    if raw.get("schemaVersion") != SCHEMA_VERSION:
        raise MaterializationError(
            "manifest_schema_mismatch",
            f"schemaVersion must be {SCHEMA_VERSION}",
            details={"actual": raw.get("schemaVersion"), "required": SCHEMA_VERSION},
        )
    project_id = _require_identity(raw.get("projectId"), "projectId")
    run_id = _require_identity(raw.get("runId"), "runId")
    plan_fingerprint = _require_identity(raw.get("planFingerprint"), "planFingerprint")
    artifacts_raw = raw.get("artifacts")
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        raise MaterializationError(
            "artifacts_absent", "Project manifest must contain at least one artifact"
        )
    artifacts = tuple(_parse_artifact(item, index) for index, item in enumerate(artifacts_raw))

    target_keys = set()
    bundle_keys = set()
    artifact_paths = {}
    for artifact in artifacts:
        target_key = (artifact.module, artifact.relative_path.casefold())
        bundle_key = artifact.bundle_entry.casefold()
        if target_key in target_keys:
            raise MaterializationError(
                "duplicate_artifact_destination",
                "Project manifest contains duplicate Windows destinations",
                details={"module": artifact.module, "relativePath": artifact.relative_path},
            )
        if bundle_key in bundle_keys:
            raise MaterializationError(
                "duplicate_bundle_entry",
                "Project manifest contains duplicate bundle entries",
                details={"bundleEntry": artifact.bundle_entry},
            )
        target_keys.add(target_key)
        bundle_keys.add(bundle_key)
        artifact_paths[artifact.relative_path.casefold()] = artifact

    expert_relative_path = _safe_relative_path(raw.get("expertRelativePath"), "expertRelativePath")
    expert_artifact = artifact_paths.get(expert_relative_path.casefold())
    if (
        expert_artifact is None
        or expert_artifact.module != "EXP"
        or not expert_relative_path.lower().endswith(".ex5")
    ):
        raise MaterializationError(
            "expert_artifact_absent",
            "expertRelativePath must identify a listed EXP .ex5 artifact",
            details={"expertRelativePath": expert_relative_path},
        )

    preset_value = raw.get("presetRelativePath")
    preset_relative_path = None
    if preset_value not in (None, ""):
        preset_relative_path = _safe_relative_path(preset_value, "presetRelativePath")
        preset_artifact = artifact_paths.get(preset_relative_path.casefold())
        if (
            preset_artifact is None
            or preset_artifact.module != "PRS"
            or not preset_relative_path.lower().endswith(".set")
        ):
            raise MaterializationError(
                "preset_artifact_absent",
                "presetRelativePath must identify a listed PRS .set artifact",
                details={"presetRelativePath": preset_relative_path},
            )

    manifest = ProjectManifest(
        raw=raw,
        project_id=project_id,
        run_id=run_id,
        plan_fingerprint=plan_fingerprint,
        expert_relative_path=expert_relative_path,
        preset_relative_path=preset_relative_path,
        artifacts=artifacts,
    )
    manifest.module_directories
    return manifest


def _snapshot_tree(root: str) -> dict[str, dict]:
    if not os.path.exists(root):
        return {}
    if not os.path.isdir(root):
        raise MaterializationError(
            "module_root_not_directory",
            "A project module destination exists but is not a directory",
            details={"path": root},
        )
    snapshot = {}
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        for directory_name in directory_names:
            directory_path = os.path.join(current_root, directory_name)
            if _is_reparse(directory_path):
                raise MaterializationError(
                    "reparse_point_in_module",
                    "A project module contains a symbolic link or junction",
                    details={"path": directory_path},
                )
        for file_name in file_names:
            file_path = os.path.join(current_root, file_name)
            if _is_reparse(file_path):
                raise MaterializationError(
                    "reparse_point_in_module",
                    "A project module contains a symbolic link or junction",
                    details={"path": file_path},
                )
            relative = os.path.relpath(file_path, root).replace(os.sep, "/")
            snapshot[relative] = {
                "size": os.path.getsize(file_path),
                "sha256": sha256_file(file_path),
            }
    return dict(sorted(snapshot.items(), key=lambda item: item[0].casefold()))


def _is_reparse(path: str) -> bool:
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _remove_path(path: str) -> None:
    if not os.path.lexists(path):
        return
    if _is_reparse(path) or os.path.isfile(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def _assert_under_root(path: str, root: str) -> None:
    absolute_path = os.path.abspath(path)
    absolute_root = os.path.abspath(root)
    try:
        common = os.path.commonpath((absolute_path, absolute_root))
    except ValueError as exc:
        raise MaterializationError(
            "destination_outside_root",
            "Project destination and module root are on different volumes",
            details={"destination": absolute_path, "root": absolute_root},
        ) from exc
    if os.path.normcase(common) != os.path.normcase(absolute_root):
        raise MaterializationError(
            "destination_outside_root",
            "Project destination is outside its declared module root",
            details={"destination": absolute_path, "root": absolute_root},
        )


class ProjectMaterialization:
    """Validated staged project with explicit apply/capture/restore lifecycle."""

    def __init__(
        self,
        manifest_path: str,
        bundle_path: str,
        stage_dir: str,
        terminal_dir: str,
        common_files_dir: str | None,
    ):
        self.manifest_path = os.path.abspath(manifest_path)
        self.bundle_path = os.path.abspath(bundle_path)
        self.stage_dir = os.path.abspath(stage_dir)
        self.terminal_dir = os.path.abspath(terminal_dir)
        self.common_files_dir = os.path.abspath(common_files_dir) if common_files_dir else None
        self.work_dir = os.path.join(self.stage_dir, "project-materialization")
        self.payload_dir = os.path.join(self.work_dir, "payload")
        self.module_stage_dir = os.path.join(self.work_dir, "modules")
        self.backup_dir = os.path.join(self.work_dir, "backups")
        self.audit_path = os.path.join(self.stage_dir, "materialization-audit.json")
        self.output_manifest_path = os.path.join(self.stage_dir, "output-manifest.json")
        self.output_archive_path = os.path.join(self.stage_dir, "output-artifacts.zip")
        self.manifest = load_project_manifest(self.manifest_path)
        self.staged_payloads: dict[str, str] = {}
        self.module_targets: dict[str, str] = {}
        self.module_stage_roots: dict[str, str] = {}
        self.backup_snapshots: dict[str, dict] = {}
        self.backup_present: dict[str, bool] = {}
        self.applied_modules: list[str] = []
        self.audit = {
            "schemaVersion": AUDIT_SCHEMA_VERSION,
            "projectId": self.manifest.project_id,
            "runId": self.manifest.run_id,
            "planFingerprint": self.manifest.plan_fingerprint,
            "createdAt": _now_iso(),
            "manifestPath": self.manifest_path,
            "manifestSha256": sha256_file(self.manifest_path),
            "bundlePath": self.bundle_path,
            "bundleSha256": sha256_file(self.bundle_path),
            "terminalDir": self.terminal_dir,
            "commonFilesDir": self.common_files_dir,
            "status": "validating",
            "artifacts": [],
            "modules": {},
            "errors": [],
        }
        self._persist_audit()
        try:
            self._stage_and_verify_bundle()
            self._build_module_trees()
            self.audit["status"] = "validated"
            self.audit["validatedAt"] = _now_iso()
            self._persist_audit()
        except Exception as exc:
            self._record_error("validation_failed", exc)
            raise

    @property
    def expert_ini_value(self) -> str:
        value = self.manifest.expert_relative_path[:-4]
        return value.replace("/", "\\")

    @property
    def preset_ini_value(self) -> str:
        if not self.manifest.preset_relative_path:
            return ""
        return self.manifest.preset_relative_path.replace("/", "\\")

    def _persist_audit(self) -> None:
        _write_json_atomic(self.audit_path, self.audit)

    def _record_error(self, phase: str, exc: Exception) -> None:
        error = exc.as_dict() if isinstance(exc, MaterializationError) else {
            "code": type(exc).__name__,
            "message": str(exc),
        }
        self.audit["errors"].append({"phase": phase, "at": _now_iso(), **error})
        self.audit["status"] = phase
        self._persist_audit()

    def _stage_and_verify_bundle(self) -> None:
        os.makedirs(self.payload_dir, exist_ok=True)
        try:
            archive = zipfile.ZipFile(self.bundle_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise MaterializationError(
                "bundle_unreadable",
                f"Project bundle is not a readable ZIP: {exc}",
                details={"path": self.bundle_path, "errorType": type(exc).__name__},
            ) from exc
        with archive:
            infos = archive.infolist()
            file_infos = {}
            for info in infos:
                if info.is_dir():
                    directory_name = info.filename.rstrip("/")
                    _safe_relative_path(directory_name, "ZIP directory entry")
                    continue
                canonical = _safe_relative_path(info.filename, "ZIP entry")
                unix_type = (info.external_attr >> 16) & stat.S_IFMT(0o177777)
                if unix_type == stat.S_IFLNK:
                    raise MaterializationError(
                        "bundle_symlink_rejected",
                        "Project bundle contains a symbolic link entry",
                        details={"bundleEntry": canonical},
                    )
                key = canonical.casefold()
                if key in file_infos:
                    raise MaterializationError(
                        "duplicate_zip_entry",
                        "Project bundle contains duplicate Windows entry names",
                        details={"bundleEntry": canonical},
                    )
                file_infos[key] = (canonical, info)

            expected_keys = {artifact.bundle_entry.casefold() for artifact in self.manifest.artifacts}
            actual_keys = set(file_infos)
            if actual_keys != expected_keys:
                raise MaterializationError(
                    "bundle_manifest_mismatch",
                    "Project bundle files do not exactly match manifest bundleEntry values",
                    details={
                        "missing": sorted(expected_keys - actual_keys),
                        "unexpected": sorted(actual_keys - expected_keys),
                    },
                )

            for index, artifact in enumerate(self.manifest.artifacts):
                _, info = file_infos[artifact.bundle_entry.casefold()]
                staged_path = os.path.join(self.payload_dir, f"{index:08d}.bin")
                digest = hashlib.sha256()
                size = 0
                with archive.open(info, "r") as source, open(staged_path, "wb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                actual_sha256 = digest.hexdigest()
                evidence = {
                    "module": artifact.module,
                    "relativePath": artifact.relative_path,
                    "bundleEntry": artifact.bundle_entry,
                    "declaredSize": artifact.size,
                    "actualSize": size,
                    "declaredSha256": artifact.sha256,
                    "actualSha256": actual_sha256,
                    "stagedPath": staged_path,
                }
                self.audit["artifacts"].append(evidence)
                if size != artifact.size or actual_sha256 != artifact.sha256:
                    self._persist_audit()
                    raise MaterializationError(
                        "artifact_integrity_mismatch",
                        "A bundled project artifact does not match its declared size and SHA-256",
                        details=evidence,
                    )
                self.staged_payloads[artifact.relative_path.casefold()] = staged_path
        self._persist_audit()

    def _module_base_root(self, module: str) -> str:
        if module == "FLS":
            if not self.common_files_dir:
                raise MaterializationError(
                    "common_files_dir_absent",
                    "FLS artifacts require an explicit MetaTrader Common Files directory",
                )
            return self.common_files_dir
        return os.path.join(self.terminal_dir, *MODULE_ROOT_PARTS[module])

    def _build_module_trees(self) -> None:
        module_directories = self.manifest.module_directories
        for module, module_directory in sorted(module_directories.items()):
            base_root = self._module_base_root(module)
            target = os.path.join(base_root, module_directory)
            _assert_under_root(target, base_root)
            if _is_reparse(target):
                raise MaterializationError(
                    "module_target_reparse_point",
                    "Remote materialization requires a physical module directory, not a junction",
                    details={"module": module, "path": target},
                )
            staged_root = os.path.join(self.module_stage_dir, module, module_directory)
            os.makedirs(staged_root, exist_ok=True)
            self.module_targets[module] = target
            self.module_stage_roots[module] = staged_root
            self.audit["modules"][module] = {
                "moduleDirectory": module_directory,
                "baseRoot": base_root,
                "targetPath": target,
                "stagedRoot": staged_root,
                "status": "staging",
            }

        for artifact in self.manifest.artifacts:
            destination = os.path.join(
                self.module_stage_roots[artifact.module],
                *PurePosixPath(artifact.path_inside_module).parts,
            )
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copyfile(
                self.staged_payloads[artifact.relative_path.casefold()], destination
            )

        for module, staged_root in self.module_stage_roots.items():
            expected = {
                artifact.path_inside_module: {
                    "size": artifact.size,
                    "sha256": artifact.sha256,
                }
                for artifact in self.manifest.artifacts
                if artifact.module == module
            }
            actual = _snapshot_tree(staged_root)
            if actual != expected:
                raise MaterializationError(
                    "staged_module_integrity_mismatch",
                    "Staged module tree differs from the validated manifest",
                    details={"module": module, "expected": expected, "actual": actual},
                )
            self.audit["modules"][module]["status"] = "validated"
            self.audit["modules"][module]["stagedSnapshot"] = actual
        self._persist_audit()

    def apply(self) -> None:
        if self.applied_modules:
            raise MaterializationError(
                "materialization_already_applied",
                "Project materialization has already been applied",
                details={"modules": list(self.applied_modules)},
            )
        self.audit["status"] = "backing_up"
        self._persist_audit()
        try:
            for module, target in sorted(self.module_targets.items()):
                base_root = os.path.dirname(target)
                os.makedirs(base_root, exist_ok=True)
                if _is_reparse(target):
                    raise MaterializationError(
                        "module_target_reparse_point",
                        "Remote materialization will not replace a junction or symbolic link",
                        details={"module": module, "path": target},
                    )
                snapshot = _snapshot_tree(target)
                self.backup_snapshots[module] = snapshot
                present = os.path.exists(target)
                self.backup_present[module] = present
                backup = os.path.join(self.backup_dir, module)
                if present:
                    os.makedirs(os.path.dirname(backup), exist_ok=True)
                    shutil.copytree(target, backup, copy_function=shutil.copyfile)
                    backup_snapshot = _snapshot_tree(backup)
                    if backup_snapshot != snapshot:
                        raise MaterializationError(
                            "backup_integrity_mismatch",
                            "Module backup differs from the pre-materialization tree",
                            details={
                                "module": module,
                                "source": snapshot,
                                "backup": backup_snapshot,
                            },
                        )
                self.audit["modules"][module]["backupPresent"] = present
                self.audit["modules"][module]["backupPath"] = backup if present else None
                self.audit["modules"][module]["backupSnapshot"] = snapshot
                self.audit["modules"][module]["status"] = "backed_up"
                self._persist_audit()

            self.audit["status"] = "applying"
            self._persist_audit()
            for module, target in sorted(self.module_targets.items()):
                _remove_path(target)
                shutil.copytree(
                    self.module_stage_roots[module], target, copy_function=shutil.copyfile
                )
                applied_snapshot = _snapshot_tree(target)
                staged_snapshot = self.audit["modules"][module]["stagedSnapshot"]
                if applied_snapshot != staged_snapshot:
                    raise MaterializationError(
                        "applied_module_integrity_mismatch",
                        "Materialized module differs from its staged source",
                        details={
                            "module": module,
                            "staged": staged_snapshot,
                            "applied": applied_snapshot,
                        },
                    )
                self.applied_modules.append(module)
                self.audit["modules"][module]["appliedSnapshot"] = applied_snapshot
                self.audit["modules"][module]["status"] = "applied"
                self._persist_audit()
            self.audit["status"] = "applied"
            self.audit["appliedAt"] = _now_iso()
            self._persist_audit()
        except Exception as exc:
            self._record_error("apply_failed", exc)
            try:
                self.restore()
            except Exception as restore_exc:
                self._record_error("rollback_failed", restore_exc)
            raise

    def capture_outputs(self) -> dict:
        records = []
        output_files = []
        if "FLS" in self.module_targets:
            target = self.module_targets["FLS"]
            current = _snapshot_tree(target)
            expected = self.audit["modules"]["FLS"]["stagedSnapshot"]
            for relative_path in sorted(
                set(current) | set(expected), key=lambda value: value.casefold()
            ):
                before = expected.get(relative_path)
                after = current.get(relative_path)
                if before is None:
                    classification = "created"
                elif after is None:
                    classification = "missing_input"
                elif before == after:
                    classification = "unchanged_input"
                else:
                    classification = "modified"
                record = {
                    "module": "FLS",
                    "relativePath": f"{self.manifest.module_directories['FLS']}/{relative_path}",
                    "classification": classification,
                    "input": before,
                    "output": after,
                }
                records.append(record)
                if classification in ("created", "modified"):
                    output_files.append((relative_path, os.path.join(target, *PurePosixPath(relative_path).parts)))

        os.makedirs(os.path.dirname(self.output_archive_path), exist_ok=True)
        with zipfile.ZipFile(
            self.output_archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            for relative_path, source_path in output_files:
                archive_name = (
                    f"FLS/{self.manifest.module_directories['FLS']}/{relative_path}"
                )
                archive.write(source_path, arcname=archive_name)

        output_manifest = {
            "schemaVersion": OUTPUT_SCHEMA_VERSION,
            "projectId": self.manifest.project_id,
            "runId": self.manifest.run_id,
            "planFingerprint": self.manifest.plan_fingerprint,
            "capturedAt": _now_iso(),
            "archivePath": self.output_archive_path,
            "archiveSize": os.path.getsize(self.output_archive_path),
            "archiveSha256": sha256_file(self.output_archive_path),
            "files": records,
        }
        _write_json_atomic(self.output_manifest_path, output_manifest)
        self.audit["outputManifestPath"] = self.output_manifest_path
        self.audit["outputManifestSha256"] = sha256_file(self.output_manifest_path)
        self.audit["outputArchivePath"] = self.output_archive_path
        self.audit["outputArchiveSha256"] = output_manifest["archiveSha256"]
        self.audit["outputsCapturedAt"] = output_manifest["capturedAt"]
        self._persist_audit()
        return output_manifest

    def restore(self) -> None:
        failures = []
        modules_to_restore = [
            module
            for module in sorted(self.module_targets, reverse=True)
            if module in self.backup_present
        ]
        self.audit["status"] = "restoring"
        self._persist_audit()
        for module in modules_to_restore:
            target = self.module_targets[module]
            backup = os.path.join(self.backup_dir, module)
            try:
                _remove_path(target)
                if self.backup_present[module]:
                    shutil.copytree(backup, target, copy_function=shutil.copyfile)
                    restored = _snapshot_tree(target)
                    expected = self.backup_snapshots[module]
                    if restored != expected:
                        raise MaterializationError(
                            "restore_integrity_mismatch",
                            "Restored module differs from its byte-exact backup",
                            details={
                                "module": module,
                                "expected": expected,
                                "restored": restored,
                            },
                        )
                elif os.path.exists(target):
                    raise MaterializationError(
                        "restore_absence_failed",
                        "A module that was absent before materialization still exists",
                        details={"module": module, "path": target},
                    )
                self.audit["modules"][module]["status"] = "restored"
                self.audit["modules"][module]["restoredSnapshot"] = (
                    _snapshot_tree(target) if self.backup_present[module] else {}
                )
            except Exception as exc:
                error = exc.as_dict() if isinstance(exc, MaterializationError) else {
                    "code": type(exc).__name__,
                    "message": str(exc),
                }
                failures.append({"module": module, **error})
                self.audit["modules"][module]["status"] = "restore_failed"
                self.audit["modules"][module]["restoreError"] = error
            self._persist_audit()
        self.applied_modules.clear()
        if failures:
            error = MaterializationError(
                "restore_failed",
                "One or more project modules could not be restored",
                details=failures,
            )
            self._record_error("restore_failed", error)
            raise error
        self.audit["status"] = "restored"
        self.audit["restoredAt"] = _now_iso()
        self._persist_audit()
