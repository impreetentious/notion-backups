from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .retention import snapshot_dirs
from .writer import create_tar_gz, directory_size_bytes, format_bytes, write_json

LOGGER = logging.getLogger(__name__)

@dataclass
class StorageResult:
    status: str
    destination: str
    uploaded_to_external: list[str] = field(default_factory=list)
    deleted_from_github: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    current_manifest: dict[str, Any] = field(default_factory=dict)

def manage_storage(config: dict[str, Any], current_snapshot_dir: Path | None = None) -> StorageResult:
    backup = config.get("backup", {})
    storage = config.get("storage", {})
    output_dir = Path(backup.get("output_dir", "exports"))
    archive_dir = Path(backup.get("archive_dir", "archives"))

    snapshots = snapshot_dirs(output_dir)
    candidates = snapshots

    external = storage.get("external_archive", {})
    result = StorageResult(
        status="success",
        destination="github",
    )

    if not external.get("enabled", False):
        result.status = "warning" if candidates else "success"
        result.warnings.extend(
            f"{snapshot.path.name} was not archived because external archival is disabled."
            for snapshot in candidates
        )
        _update_current_manifest(current_snapshot_dir, result)
        return result

    if not shutil.which(str(external.get("command", "rclone"))):
        result.status = "warning"
        result.warnings.append("External archival skipped because rclone is not installed.")
        _update_current_manifest(current_snapshot_dir, result)
        return result

    remote = external.get("remote")
    folder = str(external.get("folder", "NotionBackups")).strip("/")
    if not remote:
        result.status = "warning"
        result.warnings.append("External archival skipped because storage.external_archive.remote is not configured.")
        _update_current_manifest(current_snapshot_dir, result)
        return result

    current_dir_resolved = current_snapshot_dir.resolve() if current_snapshot_dir else None
    retained_current_manifest: dict[str, Any] | None = None
    any_upload_failed = False
    current_uploaded = False

    for snapshot in candidates:
        if not (snapshot.path / "manifest.json").exists():
            result.status = "warning"
            result.warnings.append(
                f"{snapshot.path.name} was not archived because it has no manifest.json; "
                "it looks like an incomplete snapshot left by an interrupted run."
            )
            continue
        archive_path = archive_dir / f"{snapshot.path.name}.tar.gz"
        remote_path = f"{remote}:{folder}/{archive_path.name}" if folder else f"{remote}:{archive_path.name}"
        # Finalize the snapshot manifest to its intended archived state before the
        # tar is built, so the copy uploaded to Drive carries the real storage
        # outcome instead of the pre-storage "pending" scaffold.
        finalized_manifest = _write_storage_block(
            snapshot.path,
            {
                "destination": "github+google_drive",
                "status": "success",
                "uploaded_to_external": [remote_path],
                "deleted_from_github": [],
                "warnings": [],
                "errors": [],
            },
        )
        # Always rebuild the tar via a temporary file and atomic replace: a
        # pre-existing archive could be stale or truncated by an interrupted run.
        tmp_archive_path = archive_path.with_name(archive_path.name + ".tmp")
        create_tar_gz(snapshot.path, tmp_archive_path)
        os.replace(tmp_archive_path, archive_path)
        upload = _run([str(external.get("command", "rclone")), "copyto", str(archive_path), remote_path])
        if upload.returncode:
            any_upload_failed = True
            result.status = "warning"
            result.warnings.append(f"Upload failed for {snapshot.path.name}; it was not archived.")
            result.errors.append(upload.stderr.strip() or upload.stdout.strip() or f"rclone exited {upload.returncode}")
            # The optimistic success block was wrong; rewrite the surviving
            # snapshot manifest with the failure state and drop the unsent tar.
            _update_current_manifest(snapshot.path, result)
            archive_path.unlink(missing_ok=True)
            continue
        result.uploaded_to_external.append(remote_path)
        result.destination = "github+google_drive"
        if current_dir_resolved and snapshot.path.resolve() == current_dir_resolved:
            current_uploaded = True
            retained_current_manifest = finalized_manifest
        shutil.rmtree(snapshot.path)
        result.deleted_from_github.append(snapshot.path.as_posix())
        archive_path.unlink(missing_ok=True)

    # Only age out remote archives when this run actually added a new durable
    # copy; after a failed upload, the old archives are all that is left.
    cleanup_safe = current_uploaded if current_snapshot_dir is not None else not any_upload_failed
    if cleanup_safe:
        _cleanup_external_archives(config, result)
    elif external.get("delete_remote_older_than_retention", True):
        result.warnings.append(
            "Remote retention cleanup was skipped because this run did not upload a new archive successfully."
        )
    result.destination = "github+google_drive" if result.uploaded_to_external else "github"
    # Finalize the current snapshot's manifest (and the summary fallback) with the
    # final storage outcome. When the snapshot was uploaded and removed, the
    # retained in-memory manifest keeps size/format data for notifications; when
    # an upload failed and the snapshot survived, the on-disk manifest is
    # rewritten so archival warnings/errors reach the notification step.
    _update_current_manifest(current_snapshot_dir, result, retained_current_manifest)
    return result

def _cleanup_external_archives(config: dict[str, Any], result: StorageResult) -> None:
    external = config.get("storage", {}).get("external_archive", {})
    retain_days = int(external.get("retention_days", config.get("retention", {}).get("retain_days", 30)))
    if retain_days < 10:
        retain_days = 10
    if not external.get("delete_remote_older_than_retention", True):
        return

    remote = external.get("remote")
    folder = str(external.get("folder", "NotionBackups")).strip("/")
    if not remote:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    remote_dir = f"{remote}:{folder}" if folder else f"{remote}:"
    list_result = _run([str(external.get("command", "rclone")), "lsjson", remote_dir])
    if list_result.returncode:
        result.status = "warning"
        result.warnings.append("Could not list external archives for retention cleanup.")
        return
    try:
        entries = json.loads(list_result.stdout or "[]")
    except json.JSONDecodeError:
        result.status = "warning"
        result.warnings.append("Could not parse external archive listing for retention cleanup.")
        return

    for entry in entries:
        name = entry.get("Name", "")
        modified = entry.get("ModTime")
        if not name.endswith(".tar.gz") or not modified:
            continue
        try:
            modified_at = datetime.fromisoformat(modified.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        if modified_at >= cutoff:
            continue
        delete_result = _run([str(external.get("command", "rclone")), "deletefile", f"{remote_dir}/{name}"])
        if delete_result.returncode:
            result.status = "warning"
            result.warnings.append(f"Could not delete expired external archive {name}.")

def _write_storage_block(snapshot_dir: Path, storage: dict[str, Any]) -> dict[str, Any] | None:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest["storage"] = storage
    # size_bytes contract: the snapshot payload excluding manifest.json itself,
    # matching the runner's pre-manifest measurement.
    manifest["size_bytes"] = directory_size_bytes(snapshot_dir) - manifest_path.stat().st_size
    manifest["size_human"] = format_bytes(int(manifest["size_bytes"]))
    write_json(manifest_path, manifest)
    return manifest

def _update_current_manifest(
    current_snapshot_dir: Path | None,
    result: StorageResult,
    retained_manifest: dict[str, Any] | None = None,
) -> None:
    if not current_snapshot_dir:
        return
    storage_block = {
        "destination": result.destination,
        "status": result.status,
        "uploaded_to_external": result.uploaded_to_external,
        "deleted_from_github": result.deleted_from_github,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    manifest = _write_storage_block(current_snapshot_dir, storage_block)
    if manifest is None and retained_manifest is not None:
        # The snapshot was uploaded and removed from the runner, so finalize the
        # retained in-memory copy; notifications keep real size/format metadata.
        manifest = retained_manifest
        manifest["storage"] = storage_block
    if manifest is not None:
        result.current_manifest = manifest

def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    LOGGER.info("Running storage command: %s", " ".join(command[:2]))
    return subprocess.run(command, check=False, text=True, capture_output=True)