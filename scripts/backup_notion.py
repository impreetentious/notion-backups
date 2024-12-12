from __future__ import annotations

import argparse
import logging
import os
import sys

from notion_backup.runner import run_backup

def main() -> int:
    parser = argparse.ArgumentParser(description="Create a read-only Notion backup snapshot.")
    parser.add_argument("--config", default=None, help="Path to backup config JSON.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        snapshot_dir = run_backup(args.config)
    except Exception:
        logging.exception("Notion backup failed")
        return 1

    manifest_path = snapshot_dir / "manifest.json"
    print(f"Backup snapshot written to {snapshot_dir}")
    _write_github_outputs(snapshot_dir, manifest_path)
    return 0


def _write_github_outputs(snapshot_dir, manifest_path) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"snapshot_dir={snapshot_dir.as_posix()}\n")
        handle.write(f"manifest_path={manifest_path.as_posix()}\n")


if __name__ == "__main__":
    sys.exit(main())