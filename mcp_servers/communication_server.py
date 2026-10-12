import json
import os
import re
import sys
from pathlib import Path

from fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import create_email as insert_email
from app.database import get_email as query_email


def _backend() -> str:
    if os.getenv("NORTHSTAR_SMOKE_TEST_MODE") == "1":
        return "mock"
    return os.getenv("COMMUNICATION_BACKEND", "mock").strip().lower()

mcp = FastMCP("Northstar Communication Server")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@mcp.tool
async def send_email(to: str, subject: str, body: str) -> str:
    """Send through the configured mock or Gmail communication backend."""
    if not EMAIL_PATTERN.fullmatch(to.strip()):
        return json.dumps(
            {
                "success": False,
                "error_type": "validation",
                "retryable": False,
                "message": "A valid recipient address is required.",
            }
        )
    if _backend() == "gmail":
        from app.communication.gmail import send_email as gmail_send_email

        return await gmail_send_email(to, subject, body)
    if _backend() != "mock":
        raise RuntimeError("COMMUNICATION_BACKEND must be 'mock' or 'gmail'.")
    return json.dumps(await insert_email(to, subject, body), default=str)


@mcp.tool
async def get_email(email_id: str) -> str:
    """Retrieve an email from the configured mock or Gmail backend."""
    if _backend() == "gmail":
        from app.communication.gmail import get_email as gmail_get_email

        return await gmail_get_email(email_id)
    if _backend() != "mock":
        raise RuntimeError("COMMUNICATION_BACKEND must be 'mock' or 'gmail'.")
    return json.dumps(await query_email(int(email_id)), default=str)


if __name__ == "__main__":
    mcp.run()
