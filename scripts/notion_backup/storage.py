from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .retention import _snapshot_dirs
from .writer import create_tar_gz, write_json

LOGGER = logging.getLogger(__name__)

@dataclass
class StorageResult:
    status: str
    destination: str
    retained_on_github: list[str] = field(default_factory=list)
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

    snapshots = _snapshot_dirs(output_dir)
    retained: list[Path] = []
    candidates: list[Path] = snapshots

    external = storage.get("external_archive", {})
    result = StorageResult(
        status="success",
        destination="github",
        retained_on_github=[snapshot.path.name for snapshot in retained],
    )

    if not external.get("enabled", False):
        result.status = "warning" if candidates else "success"
        result.warnings.extend(
            f"{snapshot.path.name} is older than the GitHub active set but external archival is disabled."
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
        if not archive_path.exists():
            create_tar_gz(snapshot.path, archive_path)
        remote_path = f"{remote}:{folder}/{archive_path.name}" if folder else f"{remote}:{archive_path.name}"
        upload = _run([str(external.get("command", "rclone")), "copyto", str(archive_path), remote_path])
        if upload.returncode:
            result.status = "warning"
            result.warnings.append(f"Upload failed for {snapshot.path.name}; keeping it on GitHub.")
            result.errors.append(upload.stderr.strip() or upload.stdout.strip() or f"rclone exited {upload.returncode}")
            continue
        result.uploaded_to_external.append(remote_path)
        result.destination = "github+google_drive"
        _update_current_manifest(snapshot.path, result)
        shutil.rmtree(snapshot.path)
        result.deleted_from_github.append(snapshot.path.as_posix())
        archive_path.unlink(missing_ok=True)

    _cleanup_external_archives(config, result)
    result.destination = "github+google_drive" if result.uploaded_to_external else "github"
    return result

def _cleanup_external_archives(config: dict[str, Any], result: StorageResult) -> None:
    external = config.get("storage", {}).get("external_archive", {})
    retain_days = int(external.get("retention_days", config.get("retention", {}).get("retain_days", 60)))
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

def _update_current_manifest(current_snapshot_dir: Path | None, result: StorageResult) -> None:
    if not current_snapshot_dir:
        return
    manifest_path = current_snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest["storage"] = {
        "destination": result.destination,
        "status": result.status,
        "retained_on_github": result.retained_on_github,
        "uploaded_to_external": result.uploaded_to_external,
        "deleted_from_github": result.deleted_from_github,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    manifest["size_bytes"] = _directory_size_bytes(current_snapshot_dir)
    manifest["size_human"] = _format_bytes(int(manifest["size_bytes"]))
    result.current_manifest = manifest
    write_json(manifest_path, manifest)

def _directory_size_bytes(path: Path) -> int:
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())

def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"

def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    LOGGER.info("Running storage command: %s", " ".join(command[:2]))
    return subprocess.run(command, check=False, text=True, capture_output=True)