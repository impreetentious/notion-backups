from __future__ import annotations

import unittest

from pathlib import Path
from tempfile import TemporaryDirectory

from notify import _email_body, _payload, _resolve_manifest

class NotifyStatusTests(unittest.TestCase):
    def test_success_payload_uses_manifest_size_and_google_drive_label(self) -> None:
        payload = _payload(
            "success",
            "Notion backup completed.",
            "",
            {
                "status": "success",
                "created_at": "2026-05-29T02:34:46+05:30",
                "size_bytes": 4096,
                "size_human": "4.00 KB",
                "warnings": [],
                "errors": [],
                "storage": {
                    "destination": "github+google_drive",
                    "warnings": [],
                    "errors": [],
                },
            },
            {
                "storage": {
                    "external_archive": {"enabled": True},
                }
            },
        )

        self.assertEqual(payload["backup_size"], "4.00 KB")
        self.assertEqual(payload["storage_destination"], "Google Drive Archive")
        body = _email_body(payload)
        self.assertIn("Size: 4.00 KB", body)
        self.assertIn("Storage: Google Drive Archive", body)

    def test_resolve_manifest_falls_back_to_storage_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "storage-summary.json"
            summary_path.write_text(
                '{"manifest":{"size_human":"4.00 KB","status":"success","storage":{"destination":"github+google_drive"}}}',
                encoding="utf-8",
            )

            manifest = _resolve_manifest(None, str(summary_path))
            self.assertEqual(manifest["size_human"], "4.00 KB")
            self.assertEqual(manifest["storage"]["destination"], "github+google_drive")

    def test_storage_errors_make_notification_failed(self) -> None:
        payload = _payload(
            "success",
            "Notion backup completed.",
            "",
            {
                "status": "success",
                "created_at": "2026-05-29T02:34:46+05:30",
                "warnings": [],
                "errors": [],
                "storage": {
                    "warnings": [
                        "Upload failed for NB_20260522_181026+0530; it was not archived.",
                        "Could not list external archives for retention cleanup.",
                    ],
                    "errors": [
                        'Failed to create file system for destination "notionbackups:NotionBackups/": oauth2: "invalid_grant" "Token has been expired or revoked."'
                    ],
                },
            },
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["status_label"], "Failed")

        body = _email_body(payload)
        self.assertIn("Status: Failed", body)
        self.assertIn("Warnings: 2", body)
        self.assertIn("Errors: 1", body)

    def test_warnings_without_errors_complete_with_warnings(self) -> None:
        payload = _payload(
            "success",
            "",
            "",
            {
                "status": "success",
                "created_at": "2026-05-29T02:34:46+05:30",
                "warnings": ["Recoverable issue."],
                "errors": [],
            },
        )

        self.assertEqual(payload["status"], "completed_with_warnings")
        self.assertEqual(payload["status_label"], "Completed with Warnings")
        self.assertIn("Status: Completed with Warnings", _email_body(payload))

    def test_clean_run_is_success(self) -> None:
        payload = _payload(
            "success",
            "",
            "",
            {
                "status": "success",
                "created_at": "2026-05-29T02:34:46+05:30",
                "warnings": [],
                "errors": [],
            },
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["status_label"], "Success")
        self.assertIn("Status: Success", _email_body(payload))


if __name__ == "__main__":
    unittest.main()