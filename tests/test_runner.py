from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from notion_backup.runner import BackupRunner, QueueItem, _queue_children


class QueueChildrenRootTaggingTests(unittest.TestCase):
    def test_child_page_inherits_the_root_title_of_its_owner(self) -> None:
        blocks = [{"type": "child_page", "id": "page-1", "has_children": False, "children": []}]

        children = _queue_children(blocks, owner_id="owner-1", root_title="Second Brain ⭐️")

        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].root_title, "Second Brain ⭐️")

    def test_child_database_inherits_the_root_title_of_its_owner(self) -> None:
        blocks = [
            {
                "type": "child_database",
                "id": "db-1",
                "has_children": False,
                "children": [],
                "child_database": {"title": "Habits"},
                "parent": {"type": "page_id", "page_id": "owner-1"},
            }
        ]

        children = _queue_children(blocks, owner_id="owner-1", root_title="Sage Sanctuary 🌿")

        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].root_title, "Sage Sanctuary 🌿")

    def test_nested_children_also_inherit_the_root_title(self) -> None:
        blocks = [
            {
                "type": "toggle",
                "id": "toggle-1",
                "has_children": True,
                "children": [{"type": "child_page", "id": "page-deep", "has_children": False, "children": []}],
            }
        ]

        children = _queue_children(blocks, owner_id="owner-1", root_title="Ground Zero 🌪️")

        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].object_id, "page-deep")
        self.assertEqual(children[0].root_title, "Ground Zero 🌪️")

    def test_root_title_defaults_to_empty_string(self) -> None:
        blocks = [{"type": "child_page", "id": "page-1", "has_children": False, "children": []}]

        children = _queue_children(blocks, owner_id="owner-1")

        self.assertEqual(children[0].root_title, "")


class BumpRootCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = BackupRunner(config={}, client=None)

    def test_first_bump_creates_the_bucket_with_only_that_key_set(self) -> None:
        self.runner._bump_root_count("Second Brain ⭐️", "pages")

        self.assertEqual(
            self.runner.counts_by_root["Second Brain ⭐️"],
            {"pages": 1, "databases": 0, "database_rows": 0, "blocks": 0},
        )

    def test_repeated_bumps_accumulate(self) -> None:
        self.runner._bump_root_count("Sage Sanctuary 🌿", "blocks", 40)
        self.runner._bump_root_count("Sage Sanctuary 🌿", "blocks", 60)

        self.assertEqual(self.runner.counts_by_root["Sage Sanctuary 🌿"]["blocks"], 100)

    def test_different_roots_are_tracked_independently(self) -> None:
        self.runner._bump_root_count("Second Brain ⭐️", "pages")
        self.runner._bump_root_count("Master Control ⚡️", "pages")
        self.runner._bump_root_count("Master Control ⚡️", "pages")

        self.assertEqual(self.runner.counts_by_root["Second Brain ⭐️"]["pages"], 1)
        self.assertEqual(self.runner.counts_by_root["Master Control ⚡️"]["pages"], 2)

    def test_empty_root_title_falls_back_to_a_labeled_bucket_instead_of_crashing(self) -> None:
        self.runner._bump_root_count("", "pages")

        self.assertIn("(no configured root)", self.runner.counts_by_root)
        self.assertNotIn("", self.runner.counts_by_root)


ROOT_PAGE_ID = "root-ground-zero"
CHILD_PAGE_ID = "child-page-1"
DATABASE_ID = "db-tasks"
ROW_ONE_ID = "row-1"
ROW_TWO_ID = "row-2"
ROOT_TITLE = "Ground Zero 🌪️"


def _page(page_id: str, title: str, *, workspace: bool = False) -> dict:
    return {
        "id": page_id,
        "object": "page",
        "parent": {"type": "workspace"} if workspace else {"type": "page_id", "page_id": ROOT_PAGE_ID},
        "properties": {
            "title": {
                "type": "title",
                "title": [{"type": "text", "plain_text": title, "text": {"content": title}}],
            }
        },
    }


def _paragraph(block_id: str) -> dict:
    return {
        "id": block_id,
        "object": "block",
        "type": "paragraph",
        "has_children": False,
        "paragraph": {"rich_text": []},
    }


class FullRunFakeClient:
    """Fake client covering pages, a child page, and a child database with rows."""

    notion_version = "2022-06-28"

    def retrieve_page(self, page_id: str) -> dict:
        if page_id == ROOT_PAGE_ID:
            return _page(ROOT_PAGE_ID, ROOT_TITLE, workspace=True)
        titles = {CHILD_PAGE_ID: "Field Notes", ROW_ONE_ID: "Task Alpha", ROW_TWO_ID: "Task Beta"}
        return _page(page_id, titles.get(page_id, "Untitled"))

    def list_block_children(self, block_id: str) -> list[dict]:
        if block_id == ROOT_PAGE_ID:
            return [
                _paragraph("p-1"),
                _paragraph("p-2"),
                {
                    "id": CHILD_PAGE_ID,
                    "object": "block",
                    "type": "child_page",
                    "has_children": False,
                    "child_page": {"title": "Field Notes"},
                },
                {
                    "id": DATABASE_ID,
                    "object": "block",
                    "type": "child_database",
                    "has_children": False,
                    "parent": {"type": "page_id", "page_id": ROOT_PAGE_ID},
                    "child_database": {"title": "Tasks"},
                },
            ]
        if block_id == CHILD_PAGE_ID:
            return [_paragraph("p-3")]
        return []

    def retrieve_database(self, database_id: str) -> dict:
        return {
            "id": DATABASE_ID,
            "object": "database",
            "parent": {"type": "page_id", "page_id": ROOT_PAGE_ID},
            "title": [{"plain_text": "Tasks"}],
        }

    def query_database(self, database_id: str) -> list[dict]:
        return [{"id": ROW_ONE_ID}, {"id": ROW_TWO_ID}]

    def search_all(self) -> list[dict]:
        return []

    def search_pages(self, query: str, page_size: int | None = None) -> list[dict]:
        return []


def _full_run_config(output_dir: Path) -> dict:
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
                    "title": ROOT_TITLE,
                    "top_level_only": True,
                    "enabled": True,
                }
            ],
        },
        "retention": {"enabled": False},
        "storage": {"external_archive": {"enabled": False}},
        "compression": {"enabled": False},
    }


class CountsByRootManifestTests(unittest.TestCase):
    def test_counts_by_root_shape_and_totals_in_full_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            snapshot_dir = BackupRunner(_full_run_config(output_dir), FullRunFakeClient()).run()
            manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))

        counts_by_root = manifest["counts_by_root"]

        # A single configured root, keyed by its title, with the full metric bucket.
        self.assertEqual(list(counts_by_root.keys()), [ROOT_TITLE])
        self.assertEqual(
            counts_by_root[ROOT_TITLE],
            {"pages": 4, "databases": 1, "database_rows": 2, "blocks": 5},
        )

        # Per-root totals reconcile with the flat top-level counts.
        totals = manifest["counts"]
        for metric in ("pages", "databases", "database_rows", "blocks"):
            self.assertEqual(
                sum(bucket[metric] for bucket in counts_by_root.values()),
                totals[metric],
                metric,
            )

        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["status"], "success")


if __name__ == "__main__":
    unittest.main()
