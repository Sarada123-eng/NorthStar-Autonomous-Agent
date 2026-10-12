import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from sqlalchemy import text

import app.agent.graph as graph_module
from app.agent.mcp_client import get_email, send_email
from app.agent.state import RunStatus
from app.database import engine, initialize_database


def initial_state(goal: str) -> dict:
    return {
        "run_id": str(uuid4()),
        "goal": goal,
        "messages": [],
        "status": RunStatus.RUNNING,
        "plan": [],
        "current_action": None,
        "retrieved_context": [],
        "last_tool_result": None,
        "last_tool_succeeded": None,
        "pending_human_input": None,
        "pending_human_input_type": None,
        "human_response": None,
        "verification_result": None,
        "tool_step_count": 0,
        "retry_counts": {},
        "last_action_signature": None,
        "consecutive_action_count": 0,
        "last_failed_non_retryable_action": None,
        "execution_stop_reason": None,
        "error": None,
    }


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, _messages):
        del _messages
        return next(self.responses)


class CommunicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM emails WHERE recipient LIKE 'task14-%' "
                    "OR subject = 'Welcome'"
                )
            )

    async def test_send_email_persists_through_real_mcp(self):
        result = json.loads(
            await send_email(
                "task14-tech@example.com",
                "Welcome to Northstar",
                "Your onboarding kickoff is scheduled.",
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual("sent", result["email"]["status"])
        self.assertEqual("task14-tech@example.com", result["email"]["recipient"])

        stored = json.loads(await get_email(result["email"]["id"]))
        self.assertEqual(result["email"], stored["email"])

    async def test_get_email_preserves_recipient_subject_body_and_status(self):
        created = json.loads(
            await send_email(
                "task14-contact@example.com",
                "Task 14 subject",
                "Task 14 body",
            )
        )
        result = json.loads(await get_email(created["email"]["id"]))

        self.assertTrue(result["success"])
        self.assertEqual("task14-contact@example.com", result["email"]["recipient"])
        self.assertEqual("Task 14 subject", result["email"]["subject"])
        self.assertEqual("Task 14 body", result["email"]["body"])
        self.assertEqual("sent", result["email"]["status"])

    async def test_agent_selects_send_email(self):
        find_call = {
            "name": "find_customer",
            "args": {"name": "Acme"},
            "id": "find-1",
            "type": "tool_call",
        }
        tool_call = {
            "name": "send_email",
            "args": {
                "to": "m.brody@acmecorp.com",
                "subject": "Welcome",
                "body": "Welcome to Northstar.",
            },
            "id": "email-1",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[find_call]),
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="The mock email was recorded in the outbox."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model):
            result = await graph_module.graph.ainvoke(
                initial_state("Send a welcome email to the verified contact.")
            )

        self.assertEqual(
            "The mock email was recorded in the outbox.",
            result["messages"][-1].content,
        )
        self.assertTrue(result["last_tool_succeeded"])
        emails = await get_email(
            json.loads(result["last_tool_result"]["tools"][0]["content"])["email"][
                "id"
            ]
        )
        self.assertTrue(json.loads(emails)["success"])

    async def test_missing_recipient_returns_structured_validation_failure(self):
        result = json.loads(await send_email("", "Welcome", "Hello"))

        self.assertFalse(result["success"])
        self.assertEqual("validation", result["error_type"])
        self.assertFalse(result["retryable"])


if __name__ == "__main__":
    unittest.main()
