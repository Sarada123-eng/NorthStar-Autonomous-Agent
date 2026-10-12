import base64
import json
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
DEFAULT_CREDENTIALS_PATH = Path("credentials") / "gmail_credentials.json"
DEFAULT_TOKEN_PATH = Path("credentials") / "gmail_token.json"


def _path_from_env(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser()


def _credentials() -> Credentials:
    credentials_path = _path_from_env(
        "NORTHSTAR_GMAIL_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH
    )
    token_path = _path_from_env("NORTHSTAR_GMAIL_TOKEN_PATH", DEFAULT_TOKEN_PATH)
    if not credentials_path.is_file():
        raise RuntimeError(
            "Gmail OAuth credentials are missing. Configure "
            "NORTHSTAR_GMAIL_CREDENTIALS_PATH."
        )

    credentials: Credentials | None = None
    if token_path.is_file():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if credentials.scopes and not set(SCOPES).issubset(credentials.scopes):
            credentials = None
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    elif not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(credentials_path), SCOPES
        )
        credentials = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def _service() -> Any:
    return build("gmail", "v1", credentials=_credentials(), cache_discovery=False)


def _decode_body(payload: dict[str, Any]) -> str:
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(
            payload["body"]["data"] + "=" * (-len(payload["body"]["data"]) % 4)
        ).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            return _decode_body(part)
    return ""


def _headers(message: dict[str, Any]) -> dict[str, str]:
    return {
        header["name"].lower(): header["value"]
        for header in message.get("payload", {}).get("headers", [])
    }


def _send_email_sync(to: str, subject: str, body: str) -> dict[str, Any]:
    mime = EmailMessage()
    mime["To"] = to.strip()
    mime["Subject"] = subject.strip()
    mime.set_content(body)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
    try:
        response = (
            _service()
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
    except Exception as error:
        raise RuntimeError(
            "Gmail send failed. The delivery state may be ambiguous; "
            "do not blindly resend."
        ) from error
    message_id = response.get("id")
    if not message_id:
        raise RuntimeError("Gmail send returned no message identifier.")
    return {
        "success": True,
        "backend": "gmail",
        "email_id": message_id,
        "message_id": message_id,
        "status": "sent",
        "to": to.strip(),
        "subject": subject.strip(),
    }


def _get_email_sync(email_id: str) -> dict[str, Any]:
    try:
        message = (
            _service()
            .users()
            .messages()
            .get(userId="me", id=email_id, format="full")
            .execute()
        )
    except Exception as error:
        if getattr(error, "resp", None) is not None and error.resp.status == 404:
            return {
                "success": False,
                "error_type": "not_found",
                "retryable": False,
                "message": "Gmail message was not found.",
            }
        raise RuntimeError("Gmail verification lookup failed.") from error

    headers = _headers(message)
    return {
        "success": True,
        "backend": "gmail",
        "email": {
            "id": message.get("id"),
            "recipient": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "body": _decode_body(message.get("payload", {})).rstrip("\r\n"),
            "status": "sent" if "SENT" in message.get("labelIds", []) else "received",
        },
    }


async def send_email(to: str, subject: str, body: str) -> str:
    import asyncio

    return json.dumps(await asyncio.to_thread(_send_email_sync, to, subject, body))


async def get_email(email_id: str) -> str:
    import asyncio

    return json.dumps(await asyncio.to_thread(_get_email_sync, str(email_id)))
