from __future__ import annotations

import os
import ssl
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from notify import _email_body, _payload, _resolve_manifest, _send_email, _send_webhook

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

    def test_payload_uses_configured_timezone_for_timestamp(self) -> None:
        payload = _payload(
            "success",
            "",
            "",
            {"status": "success", "created_at": "2026-05-29T02:34:46+00:00", "warnings": [], "errors": []},
            {"backup": {"timezone": "America/New_York"}},
        )

        # 02:34 UTC is the previous evening in New York (EDT), not IST.
        self.assertEqual(payload["timestamp"], "28-05-2026_22:34:46_EDT")


def _minimal_payload() -> dict:
    return {"status_label": "Success", "warnings": [], "errors": []}


class ChannelCredentialTests(unittest.TestCase):
    @patch("notify.smtplib.SMTP")
    def test_email_starttls_uses_a_certificate_verifying_context(self, smtp_mock) -> None:
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "user",
            "SMTP_PASSWORD": "secret",
            "NOTIFY_EMAIL_TO": "owner@example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            sent = _send_email({"type": "smtp_email"}, _minimal_payload())

        self.assertTrue(sent)
        smtp = smtp_mock.return_value.__enter__.return_value
        smtp.starttls.assert_called_once()
        context = smtp.starttls.call_args.kwargs["context"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_enabled_email_channel_fails_when_required_settings_are_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            sent = _send_email({"type": "smtp_email"}, _minimal_payload())

        self.assertFalse(sent)

    def test_enabled_webhook_channel_fails_when_url_is_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            sent = _send_webhook({"type": "webhook"}, _minimal_payload())

        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()