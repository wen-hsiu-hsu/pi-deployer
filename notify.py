"""Telegram notification for pi-deployer."""

import logging
import os

import requests

logger = logging.getLogger("pi-deployer")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_notification(event_type, project, commit_info=None, details=None):
    """Send a Telegram notification.

    Args:
        event_type: One of "triggered", "success", "failed", "timeout".
        project: Project config dict (must have "name").
        commit_info: Optional dict with "message", "author", "url".
        details: Optional string with extra details (e.g. error log tail).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.debug("Telegram not configured, skipping notification")
        return

    message = _format_message(event_type, project, commit_info, details)

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if not resp.ok:
            logger.warning("Telegram API error: %s %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.warning("Telegram notification failed: %s", e)


def _format_message(event_type, project, commit_info, details):
    icons = {
        "triggered": "[DEPLOY]",
        "success": "[OK]",
        "failed": "[FAIL]",
        "timeout": "[TIMEOUT]",
    }
    icon = icons.get(event_type, "[INFO]")
    project_name = project.get("name", "unknown")

    lines = [f"<b>{icon} {project_name}</b>"]

    if commit_info:
        author = commit_info.get("author", "unknown")
        message = _escape_html(commit_info.get("message", ""))
        url = commit_info.get("url", "")
        lines.append(f"Commit by <b>{_escape_html(author)}</b>: {message}")
        if url:
            lines.append(f'<a href="{url}">View commit</a>')

    if event_type == "failed" and details:
        truncated = details[-500:] if len(details) > 500 else details
        lines.append(f"\n<pre>{_escape_html(truncated)}</pre>")
    elif event_type == "timeout":
        lines.append("Deploy timed out")
    elif details:
        lines.append(_escape_html(details))

    return "\n".join(lines)


def _escape_html(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
