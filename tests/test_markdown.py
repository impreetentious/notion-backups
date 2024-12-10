from __future__ import annotations

import unittest

from notion_backup.markdown import _block_to_lines


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
