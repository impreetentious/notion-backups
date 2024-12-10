from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
