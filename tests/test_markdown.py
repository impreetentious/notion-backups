from __future__ import annotations

import unittest

from notion_backup.markdown import _block_to_lines, blocks_to_markdown


def _numbered(text: str) -> dict:
    return {
        "type": "numbered_list_item",
        "id": text,
        "has_children": False,
        "children": [],
        "numbered_list_item": {"rich_text": [{"plain_text": text, "annotations": {}}]},
    }


def _para(text: str) -> dict:
    return {
        "type": "paragraph",
        "id": text,
        "has_children": False,
        "children": [],
        "paragraph": {"rich_text": [{"plain_text": text, "annotations": {}}]},
    }


class NumberedListOrdinalTests(unittest.TestCase):
    def test_list_after_a_paragraph_starts_at_one(self) -> None:
        markdown = blocks_to_markdown([_para("Intro"), _numbered("a"), _numbered("b"), _numbered("c")])

        self.assertIn("1. a", markdown)
        self.assertIn("2. b", markdown)
        self.assertIn("3. c", markdown)
        self.assertNotIn("4.", markdown)

    def test_a_new_run_restarts_numbering(self) -> None:
        markdown = blocks_to_markdown([_numbered("first"), _para("break"), _numbered("second")])

        lines = [line for line in markdown.splitlines() if line.strip()]
        self.assertEqual(lines, ["1. first", "break", "1. second"])


class TableRowRenderingTests(unittest.TestCase):
    def test_multiple_cells_render_as_separate_columns(self) -> None:
        block = {
            "type": "table_row",
            "table_row": {
                "cells": [
                    [{"plain_text": "Name", "annotations": {}}],
                    [{"plain_text": "Status", "annotations": {}}],
                    [{"plain_text": "Owner", "annotations": {}}],
                ]
            },
        }

        lines = _block_to_lines(block, depth=0, index=1)

        self.assertEqual(lines, ["| Name | Status | Owner |"])

    def test_single_cell_row_still_renders_correctly(self) -> None:
        block = {
            "type": "table_row",
            "table_row": {"cells": [[{"plain_text": "Only cell", "annotations": {}}]]},
        }

        lines = _block_to_lines(block, depth=0, index=1)

        self.assertEqual(lines, ["| Only cell |"])

    def test_empty_cell_renders_as_an_empty_column_not_a_dropped_one(self) -> None:
        block = {
            "type": "table_row",
            "table_row": {
                "cells": [
                    [{"plain_text": "Name", "annotations": {}}],
                    [],
                    [{"plain_text": "Owner", "annotations": {}}],
                ]
            },
        }

        lines = _block_to_lines(block, depth=0, index=1)

        self.assertEqual(lines, ["| Name |  | Owner |"])


if __name__ == "__main__":
    unittest.main()
