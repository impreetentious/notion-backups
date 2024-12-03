#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from notion_backup.config import load_config
from notion_backup.retention import apply_retention


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply backup retention without creating a new snapshot.")
    parser.add_argument("--config", default=None, help="Path to backup config JSON.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        config = load_config(args.config)
        retention = config.get("retention", {})
        backup = config.get("backup", {})
        if not retention.get("enabled", True):
            logging.info("Retention is disabled")
            return 0
        deleted = apply_retention(
            output_dir=Path(backup.get("output_dir", "exports")),
            archive_dir=Path(backup.get("archive_dir", "archives")),
            retain_days=int(retention.get("retain_days", 60)),
            min_snapshots=int(retention.get("min_snapshots", 8)),
        )
        print(f"Deleted {len(deleted)} expired backup paths")
        return 0
    except Exception:
        logging.exception("Retention failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
