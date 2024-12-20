from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("config/backup_config.json")


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    config_path = Path(path or os.getenv("BACKUP_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        raise FileNotFoundError(f"Backup config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    _apply_env_overrides(config)
    _validate_config(config, config_path)
    return config


def _apply_env_overrides(config: dict[str, Any]) -> None:
    if os.getenv("BACKUP_OUTPUT_DIR"):
        config.setdefault("backup", {})["output_dir"] = os.environ["BACKUP_OUTPUT_DIR"]
    if os.getenv("BACKUP_ARCHIVE_DIR"):
        config.setdefault("backup", {})["archive_dir"] = os.environ["BACKUP_ARCHIVE_DIR"]
    if os.getenv("RETENTION_DAYS"):
        config.setdefault("retention", {})["retain_days"] = max(1, int(os.environ["RETENTION_DAYS"]))
    if os.getenv("BACKUP_COMPRESSION_ENABLED"):
        value = os.environ["BACKUP_COMPRESSION_ENABLED"].strip().lower()
        config.setdefault("compression", {})["enabled"] = value in {"1", "true", "yes", "on"}

def _validate_config(config: dict[str, Any], path: Path) -> None:
    backup = config.get("backup")
    if not isinstance(backup, dict):
        raise ValueError(f"{path}: missing object: backup")

    roots = backup.get("roots", [])
    if not isinstance(roots, list):
        raise ValueError(f"{path}: backup.roots must be a list")

    for index, root in enumerate(roots):
        if not isinstance(root, dict):
            raise ValueError(f"{path}: backup.roots[{index}] must be an object")
        if root.get("enabled", True) is False:
            continue
        if root.get("type") not in {"page", "database"}:
            raise ValueError(f"{path}: backup.roots[{index}].type must be page or database")
        if root.get("type") == "database" and not root.get("id") and not root.get("id_env"):
            # The runner resolves title-only roots for pages only; a title-only
            # database root would pass validation and then fail mid-run.
            raise ValueError(f"{path}: backup.roots[{index}] database roots need id or id_env")
        if not root.get("id") and not root.get("id_env") and not root.get("title"):
            raise ValueError(f"{path}: backup.roots[{index}] needs id, id_env, or title")

    scope = backup.get("scope", {})
    if scope:
        mode = scope.get("mode", "configured_roots")
        if mode not in {"configured_roots", "all_top_level_pages"}:
            raise ValueError(f"{path}: backup.scope.mode must be configured_roots or all_top_level_pages")
        if mode == "all_top_level_pages" and any(
            isinstance(root, dict) and root.get("enabled", True) for root in roots
        ):
            raise ValueError(
                f"{path}: backup.scope.mode=all_top_level_pages conflicts with enabled backup.roots; "
                "disable the roots or use configured_roots"
            )

    retention = config.get("retention", {})
    if retention.get("enabled", True):
        retain_days = int(retention.get("retain_days", 30))
        if retain_days < 1:
            raise ValueError(f"{path}: retention.retain_days must be at least 1")

def enabled_roots(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [root for root in config.get("backup", {}).get("roots", []) if root.get("enabled", True)]