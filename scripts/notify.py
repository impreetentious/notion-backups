#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib import error, request

from notion_backup.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Send backup status notifications.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--status", required=True, choices=["success", "failure"])
    parser.add_argument("--run-url", default=os.getenv("GITHUB_SERVER_URL", ""))
    parser.add_argument("--message", default="")
    parser.add_argument("--manifest", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    manifest = _load_manifest(args.manifest)
    payload = _payload(args.status, args.message, args.run_url, manifest)

    failures = 0
    for channel in config.get("notifications", {}).get("channels", []):
        if not channel.get("enabled", False):
            continue
        channel_type = channel.get("type")
        if channel_type == "github_step_summary":
            _write_step_summary(payload)
        elif channel_type == "webhook":
            failures += 0 if _send_webhook(channel, payload) else 1
        elif channel_type == "smtp_email":
            failures += 0 if _send_email(channel, payload) else 1
        else:
            print(f"Unknown notification channel type: {channel_type}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


def _payload(status: str, message: str, base_url: str, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest_status = manifest.get("status")
    warnings = manifest.get("warnings", []) + manifest.get("storage", {}).get("warnings", [])
    errors = manifest.get("errors", []) + manifest.get("storage", {}).get("errors", [])
    effective_status = _notification_status(status, manifest_status, warnings, errors)
    backup_ts_raw = (
        manifest.get("timestamp")
        or manifest.get("created_at")
        or datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
    )
    backup_dt = datetime.fromisoformat(backup_ts_raw.replace("Z", "+00:00")).astimezone(
        ZoneInfo("Asia/Kolkata")
    )

    return {
        "service": "notion-backup",
        "status": effective_status,
        "status_label": _status_label(effective_status),
        "message": message or f"Notion Backup {_status_label(effective_status)}",
        "repository": os.getenv("GITHUB_REPOSITORY", ""),
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "run_url": _github_run_url(base_url),
        "timestamp": backup_dt.strftime("%d-%m-%Y_%H:%M:%S_%Z"),
        "backup_version": f"NB_v{backup_dt.strftime('%Y.%m.%d')}",
        "format_version": manifest.get("format_version") or manifest.get("format", {}).get("version", ""),
        "backup_size": manifest.get("size_human", ""),
        "backup_size_bytes": manifest.get("size_bytes", ""),
        "storage_destination": "GitHub + Drive",
        "warnings": warnings,
        "errors": errors,
    }


def _notification_status(
    workflow_status: str,
    manifest_status: str | None,
    warnings: list[Any],
    errors: list[Any],
) -> str:
    if errors:
        return "failed"
    if workflow_status == "failure" or manifest_status in {"failure", "failed", "partial"}:
        return "failed"
    if warnings or manifest_status == "warning":
        return "completed_with_warnings"
    return "success"


def _status_label(status: str) -> str:
    return {
        "completed_with_warnings": "Completed with Warnings",
        "failed": "Failed",
        "failure": "Failed",
        "success": "Success",
        "warning": "Completed with Warnings",
    }.get(status, status.replace("_", " ").title())


def _github_run_url(base_url: str) -> str:
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if repo and run_id and base_url:
        return f"{base_url.rstrip('/')}/{repo}/actions/runs/{run_id}"
    return ""


def _load_manifest(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    manifest_path = Path(path)
    if not manifest_path.exists():
        return {}
    with manifest_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_step_summary(payload: dict[str, Any]) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    lines = [
        f"## Notion Backup Status: {payload['status_label']}",
        "",
        f"- Version: `{payload.get('backup_version') or 'unknown'}`",
        f"- Format: `{payload.get('format_version') or 'unknown'}`",
        f"- Size: `{payload.get('backup_size') or 'unknown'}`",
        f"- Timestamp: `{payload.get('timestamp')}`",
        f"- Storage: `{payload.get('storage_destination') or 'unknown'}`",
        
        "",
        f"- Run: {payload.get('run_url') or 'local'}",
    ]
    line = "\n".join(lines)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        print(line)


def _send_webhook(channel: dict[str, Any], payload: dict[str, Any]) -> bool:
    url_env = channel.get("url_env", "NOTIFY_WEBHOOK_URL")
    url = os.getenv(url_env, "")
    if not url:
        print(f"Webhook notification skipped because {url_env} is not set")
        return True

    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            response.read()
        return True
    except error.URLError as exc:
        print(f"Webhook notification failed: {exc}", file=sys.stderr)
        return False


def _send_email(channel: dict[str, Any], payload: dict[str, Any]) -> bool:
    host = os.getenv(channel.get("host_env", "SMTP_HOST"), "")
    username = os.getenv(channel.get("username_env", "SMTP_USERNAME"), "")
    password = os.getenv(channel.get("password_env", "SMTP_PASSWORD"), "")
    from_addr = os.getenv(channel.get("from_env", "NOTIFY_EMAIL_FROM"), "") or username
    to_addr = os.getenv(channel.get("to_env", "NOTIFY_EMAIL_TO"), "")
    port_value = os.getenv(channel.get("port_env", "SMTP_PORT"), "") or str(channel.get("port", 587))
    port = int(port_value)
    use_tls = (os.getenv(channel.get("tls_env", "SMTP_USE_TLS"), "") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    missing = [name for name, value in {"SMTP_HOST": host, "SMTP_USERNAME": username, "SMTP_PASSWORD": password, "NOTIFY_EMAIL_TO": to_addr}.items() if not value]
    if missing:
        print(f"Email notification skipped because required settings are missing: {', '.join(missing)}")
        return True

    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_addr
    message["Subject"] = f"Notion Backup: {payload['status_label']}"
    message.set_content(_email_body(payload))

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
        return True
    except OSError as exc:
        print(f"Email notification failed: {exc}", file=sys.stderr)
        return False


def _email_body(payload: dict[str, Any]) -> str:
    warnings = payload.get("warnings", [])
    errors = payload.get("errors", [])
    lines = [
        f"Version: {payload.get('backup_version') or 'unknown'}",
        f"Format Version: {payload.get('format_version') or 'unknown'}",
        f"Timestamp: {payload.get('timestamp')}",
        f"Size: {payload.get('backup_size') or 'unknown'}",
        f"Storage: {payload.get('storage_destination') or 'unknown'}",
        f"Warnings: {len(warnings)}",
        f"Errors: {len(errors)}",
        "",
        f"Status: {payload['status_label']}",
        f"Run: {payload.get('run_url') or 'local'}",
    ]
    if warnings:
        lines.append("")
        lines.append("Warning Details:")
        lines.extend(f"- {item.get('code', 'warning')}: {item.get('message', item)}" if isinstance(item, dict) else f"- {item}" for item in warnings[:10])
    if errors:
        lines.append("")
        lines.append("Error Details:")
        lines.extend(f"- {item.get('code', 'error')}: {item.get('message', item)}" if isinstance(item, dict) else f"- {item}" for item in errors[:10])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
