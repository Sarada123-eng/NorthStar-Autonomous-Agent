import json
import os
import unittest
from pathlib import Path
from uuid import uuid4

from app.agent.mcp_client import get_email, send_email


def _configured() -> bool:
    credentials = Path(
        os.getenv(
            "NORTHSTAR_GMAIL_CREDENTIALS_PATH",
            "credentials/gmail_credentials.json",
        )
    )
    return (
        os.getenv("NORTHSTAR_GMAIL_INTEGRATION_TESTS") == "1"
        and credentials.is_file()
        and bool(os.getenv("NORTHSTAR_GMAIL_TEST_RECIPIENT"))
    )


@unittest.skipUnless(
    _configured(),
    "Gmail integration tests skipped: enable NORTHSTAR_GMAIL_INTEGRATION_TESTS=1, "
    "provide OAuth credentials, and set NORTHSTAR_GMAIL_TEST_RECIPIENT.",
)
class GmailIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_authentication_and_send_verification(self):
        recipient = os.environ["NORTHSTAR_GMAIL_TEST_RECIPIENT"]
        subject = f"Northstar Gmail integration {uuid4()}"
        body = "Controlled integration-test message."

        original_backend = os.environ.get("COMMUNICATION_BACKEND")
        os.environ["COMMUNICATION_BACKEND"] = "gmail"
        try:
            sent = json.loads(await send_email(recipient, subject, body))
            self.assertTrue(sent["success"])
            self.assertEqual("gmail", sent["backend"])
            self.assertTrue(sent["message_id"])

            retrieved = json.loads(await get_email(sent["message_id"]))
        finally:
            if original_backend is None:
                os.environ.pop("COMMUNICATION_BACKEND", None)
            else:
                os.environ["COMMUNICATION_BACKEND"] = original_backend

        self.assertTrue(retrieved["success"])
        self.assertEqual(sent["message_id"], retrieved["email"]["id"])
        self.assertEqual(recipient, retrieved["email"]["recipient"])
        self.assertEqual(subject, retrieved["email"]["subject"])
        self.assertEqual(body, retrieved["email"]["body"].strip())
        self.assertEqual("sent", retrieved["email"]["status"])


if __name__ == "__main__":
    unittest.main()
