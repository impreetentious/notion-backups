from __future__ import annotations

import json
import logging
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

    for snapshot in candidates:
        archive_path = archive_dir / f"{snapshot.path.name}.tar.gz"
        remote_path = f"{remote}:{folder}/{archive_path.name}" if folder else f"{remote}:{archive_path.name}"
        # Finalize the snapshot manifest to its intended archived state before the
        # tar is built, so the copy uploaded to Drive carries the real storage
        # outcome instead of the pre-storage "pending" scaffold.
        _write_storage_block(
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
        if not archive_path.exists():
            create_tar_gz(snapshot.path, archive_path)
        upload = _run([str(external.get("command", "rclone")), "copyto", str(archive_path), remote_path])
        if upload.returncode:
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
        shutil.rmtree(snapshot.path)
        result.deleted_from_github.append(snapshot.path.as_posix())
        archive_path.unlink(missing_ok=True)

    _cleanup_external_archives(config, result)
    result.destination = "github+google_drive" if result.uploaded_to_external else "github"
    # Finalize the current snapshot's manifest (and the summary fallback) with the
    # final storage outcome. This is a no-op when the snapshot was uploaded and
    # removed, but is essential when an upload failed and the snapshot survived:
    # without it, archival warnings/errors never reach the notification step.
    _update_current_manifest(current_snapshot_dir, result)
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
        result.warnings.append("Could not list external archives for retention cleanup.")
        return
    try:
        entries = json.loads(list_result.stdout or "[]")
    except json.JSONDecodeError:
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
            result.warnings.append(f"Could not delete expired external archive {name}.")

def _write_storage_block(snapshot_dir: Path, storage: dict[str, Any]) -> dict[str, Any] | None:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest["storage"] = storage
    manifest["size_bytes"] = directory_size_bytes(snapshot_dir)
    manifest["size_human"] = format_bytes(int(manifest["size_bytes"]))
    write_json(manifest_path, manifest)
    return manifest

def _update_current_manifest(current_snapshot_dir: Path | None, result: StorageResult) -> None:
    if not current_snapshot_dir:
        return
    manifest = _write_storage_block(
        current_snapshot_dir,
        {
            "destination": result.destination,
            "status": result.status,
            "uploaded_to_external": result.uploaded_to_external,
            "deleted_from_github": result.deleted_from_github,
            "warnings": result.warnings,
            "errors": result.errors,
        },
    )
    if manifest is not None:
        result.current_manifest = manifest

def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    LOGGER.info("Running storage command: %s", " ".join(command[:2]))
    return subprocess.run(command, check=False, text=True, capture_output=True)