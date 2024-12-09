from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from notion_backup.config import enabled_roots, load_config
from notion_backup.notion_client import NotionApiError
from notion_backup.runner import BackupRunner

class ConfigTests(unittest.TestCase):
    def test_configured_roots_are_the_three_master_pages(self) -> None:
        config = load_config("config/backup_config.json")
        roots = enabled_roots(config)

        self.assertEqual([root["title"] for root in roots], ["Sage Sanctuary 🌿", "Command Centre 🚀", "Ground Zero 🌪️"])
        self.assertEqual(config["backup"]["scope"]["mode"], "configured_roots")
        self.assertNotIn("Clarity", json.dumps(config))

    def test_github_keep_zero_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "backup": {"roots": [], "scope": {"mode": "configured_roots"}},
                        "retention": {"enabled": True, "retain_days": 30},
                        "storage": {"github": {"keep_latest_snapshots": 0}},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(path)
            self.assertEqual(
                config.get("storage", {}).get("github", {}).get("keep_latest_snapshots"), 0
            )


ROOT_PAGE_ID = "351733f6-271e-811f-bd0f-fd7b50bb8cfa"
LINKED_VIEW_ID = "df7733f6-271e-8300-b4f5-0191b83c0eb8"


class RunnerLinkedViewTests(unittest.TestCase):
    def test_linked_view_wrappers_do_not_mark_manifest_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            client = FakeNotionClient(linked_view=True)
            config = _runner_config(output_dir)

            snapshot_dir = BackupRunner(config, client).run()
            manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["counts"]["linked_views"], 1)
            self.assertEqual(manifest["counts"]["skipped_child_databases"], 0)
            self.assertEqual(manifest["warnings"], [])

            linked_view = next(
                item for item in manifest["objects"] if item["id"] == LINKED_VIEW_ID and item["type"] == "linked_database_view"
            )
            self.assertIn("reference", linked_view["files"])
            self.assertTrue((snapshot_dir / linked_view["files"]["reference"]).exists())

    def test_genuine_inaccessible_child_databases_still_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            client = FakeNotionClient(linked_view=False)
            config = _runner_config(output_dir)

            snapshot_dir = BackupRunner(config, client).run()
            manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["status"], "warning")
            self.assertEqual(manifest["counts"]["linked_views"], 0)
            self.assertEqual(manifest["counts"]["skipped_child_databases"], 1)
            self.assertEqual(manifest["warnings"][0]["code"], "child_database_inaccessible")


class FakeNotionClient:
    notion_version = "2022-06-28"

    def __init__(self, *, linked_view: bool) -> None:
        self.linked_view = linked_view

    def retrieve_page(self, page_id: str) -> dict:
        if page_id != ROOT_PAGE_ID:
            raise AssertionError(f"Unexpected page lookup: {page_id}")
        return {
            "id": ROOT_PAGE_ID,
            "object": "page",
            "parent": {"type": "workspace"},
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"type": "text", "plain_text": "Ground Zero 🌪️", "text": {"content": "Ground Zero 🌪️"}}],
                }
            },
        }

    def list_block_children(self, block_id: str) -> list[dict]:
        if block_id != ROOT_PAGE_ID:
            return []
        return [
            {
                "id": LINKED_VIEW_ID,
                "object": "block",
                "type": "child_database",
                "has_children": False,
                "parent": {"type": "page_id", "page_id": ROOT_PAGE_ID},
                "child_database": {"title": "Task Planner"},
            }
        ]

    def retrieve_database(self, database_id: str) -> dict:
        if self.linked_view:
            raise NotionApiError(
                "Notion API GET /databases/df7733f6-271e-8300-b4f5-0191b83c0eb8 failed: HTTP 400",
                status_code=400,
                body={
                    "object": "error",
                    "status": 400,
                    "code": "validation_error",
                    "message": (
                        "Database with ID df7733f6-271e-8300-b4f5-0191b83c0eb8 does not contain any "
                        "data sources accessible by this API bot."
                    ),
                },
            )
        raise NotionApiError(
            "Notion API GET /databases/df7733f6-271e-8300-b4f5-0191b83c0eb8 failed: HTTP 403",
            status_code=403,
            body={"object": "error", "status": 403, "code": "forbidden", "message": "Forbidden"},
        )

    def query_database(self, database_id: str) -> list[dict]:
        raise AssertionError(f"query_database should not be called for {database_id}")

    def search_all(self) -> list[dict]:
        return []

    def search_pages(self, query: str, page_size: int | None = None) -> list[dict]:
        return []


def _runner_config(output_dir: Path) -> dict:
    return {
        "backup": {
            "timezone": "Asia/Kolkata",
            "output_dir": str(output_dir),
            "archive_dir": str(output_dir.parent / "archives"),
            "include_all_accessible": False,
            "scope": {"mode": "configured_roots"},
            "traversal": {
                "continue_on_object_error": True,
                "fail_on_root_object_error": True,
                "skip_inaccessible_child_databases": True,
            },
            "roots": [
                {
                    "type": "page",
                    "id": ROOT_PAGE_ID,
                    "title": "Ground Zero 🌪️",
                    "top_level_only": True,
                    "enabled": True,
                }
            ],
        },
        "retention": {"enabled": False},
        "storage": {"external_archive": {"enabled": False}},
        "compression": {"enabled": False},
    }


if __name__ == "__main__":
    unittest.main()