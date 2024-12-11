from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from notion_backup.config import load_config
from notion_backup.storage import manage_storage

def main() -> int:
    parser = argparse.ArgumentParser(description="Archive older backups externally and keep GitHub small.")
    parser.add_argument("--config", default=None, help="Path to backup config JSON.")
    parser.add_argument("--current-snapshot", default=None, help="Current snapshot directory to update with storage status.")
    parser.add_argument("--summary-file", default=None, help="Optional JSON path for storage result.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
        result = manage_storage(config, Path(args.current_snapshot) if args.current_snapshot else None)
        payload = {
            "status": result.status,
            "destination": result.destination,
            "uploaded_to_external": result.uploaded_to_external,
            "deleted_from_github": result.deleted_from_github,
            "warnings": result.warnings,
            "errors": result.errors,
            "manifest": result.current_manifest,
        }
        if args.summary_file:
            summary_path = Path(args.summary_file)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(payload, sort_keys=True))
        return 0 if result.status in {"success", "warning"} else 1
    except Exception:
        logging.exception("Storage management failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())