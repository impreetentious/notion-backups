from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)


def apply_retention(
    output_dir: Path,
    archive_dir: Path,
    retain_days: int,
    min_snapshots: int = 8,
    now: datetime | None = None,
) -> list[Path]:
    if retain_days < 60:
        raise ValueError("retain_days must be at least 60")

    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    cutoff = current_time - timedelta(days=retain_days)
    snapshots = _snapshot_dirs(output_dir)
    deletable = [snapshot for snapshot in snapshots if snapshot.created_at < cutoff]
    keep_count = max(min_snapshots, 0)
    max_deletions = max(len(snapshots) - keep_count, 0)
    to_delete = deletable[:max_deletions]

    deleted: list[Path] = []
    for snapshot in to_delete:
        LOGGER.info("Removing expired backup snapshot: %s", snapshot.path)
        shutil.rmtree(snapshot.path)
        deleted.append(snapshot.path)

        archive_path = archive_dir / f"{snapshot.path.name}.tar.gz"
        if archive_path.exists():
            LOGGER.info("Removing expired backup archive: %s", archive_path)
            archive_path.unlink()
            deleted.append(archive_path)

    return deleted


class Snapshot:
    def __init__(self, path: Path, created_at: datetime) -> None:
        self.path = path
        self.created_at = created_at


def _snapshot_dirs(output_dir: Path) -> list[Snapshot]:
    if not output_dir.exists():
        return []

    snapshots: list[Snapshot] = []
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        created_at = _created_at(child)
        if created_at:
            snapshots.append(Snapshot(child, created_at))

    snapshots.sort(key=lambda item: item.created_at)
    return snapshots


def _created_at(snapshot_dir: Path) -> datetime | None:
    manifest_path = snapshot_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            value = manifest.get("created_at") or manifest.get("timestamp")
            if isinstance(value, str):
                return _normalize_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except (OSError, ValueError, json.JSONDecodeError):
            LOGGER.warning("Could not parse snapshot manifest timestamp: %s", manifest_path)

    try:
        return _normalize_datetime(_parse_snapshot_dir_name(snapshot_dir.name))
    except ValueError:
        LOGGER.warning("Skipping unrecognized backup snapshot directory: %s", snapshot_dir)
        return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_snapshot_dir_name(name: str) -> datetime:
    normalized = name.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    if name.startswith("NB_"):
        return datetime.strptime(name, "NB_%Y%m%d_%H%M%S%z")

    # Legacy runner snapshot IDs use YYYY-MM-DDTHH-MM-SS+ZZZZ to avoid colons in paths.
    if len(name) >= 19 and name[10] == "T" and name[13] == "-" and name[16] == "-":
        for pattern in ("%Y-%m-%dT%H-%M-%S%z", "%Y-%m-%dT%H-%M-%S"):
            try:
                return datetime.strptime(name, pattern)
            except ValueError:
                continue

    raise ValueError(f"Unsupported snapshot timestamp: {name}")
