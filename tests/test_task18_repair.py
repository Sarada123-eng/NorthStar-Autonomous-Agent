import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.types import Command

import app.agent.graph as graph_module
from app.agent.state import RunStatus
from app.policy.communication import ALLOW, APPROVAL_REQUIRED, DENY


def state() -> dict:
    return {
        "run_id": str(uuid4()),
        "goal": "Send the project update.",
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
        "pending_approval_action": None,
        "verification_result": None,
        "verification_repair_count": 0,
        "tool_step_count": 0,
        "retry_counts": {},
        "last_action_signature": None,
        "consecutive_action_count": 0,
        "last_failed_non_retryable_action": None,
        "execution_stop_reason": None,
        "error": None,
    }


def call():
    return {
        "name": "send_email",
        "args": {
            "to": "verified@acmecorp.com",
            "subject": "Task 18",
            "body": "Correct content.",
        },
        "id": "task18-email",
        "type": "tool_call",
    }


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        return next(self.responses)


def persisted(email_id, *, recipient=None, subject=None, body=None):
    original = call()["args"]
    return json.dumps(
        {
            "success": True,
            "email": {
                "id": email_id,
                "recipient": recipient or original["to"],
                "subject": subject or original["subject"],
                "body": body or original["body"],
                "status": "sent",
            },
        }
    )


class RepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_failure_repairs_and_verifies(self):
        send = AsyncMock(
            side_effect=[
                json.dumps({"success": True, "email_id": 1}),
                json.dumps({"success": True, "email_id": 2}),
            ]
        )
        read = AsyncMock(
            side_effect=[
                persisted(1, recipient="wrong@example.com"),
                persisted(2),
            ]
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call()]),
                AIMessage(content="Repair completed and verified."),
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "evaluate_email", return_value={"decision": ALLOW}
        ), patch.object(graph_module, "call_send_email", send), patch.object(
            graph_module, "call_get_email", read
        ):
            result = await graph_module.graph.ainvoke(state())

        self.assertEqual("PASS", result["verification_result"]["status"])
        self.assertEqual(1, result["verification_repair_count"])
        self.assertEqual(2, send.await_count)

    async def test_repair_is_policy_checked_and_denied(self):
        send = AsyncMock(return_value=json.dumps({"success": True, "email_id": 1}))
        read = AsyncMock(return_value=persisted(1, subject="Wrong"))
        policy = iter(
            [
                {"decision": ALLOW},
                {"decision": DENY, "reason": "Protected content."},
            ]
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call()]),
                AIMessage(content="Repair was denied."),
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "evaluate_email", side_effect=lambda **_: next(policy)
        ), patch.object(graph_module, "call_send_email", send), patch.object(
            graph_module, "call_get_email", read
        ):
            result = await graph_module.graph.ainvoke(state())

        send.assert_awaited_once()
        self.assertEqual("communication_denied", result["execution_stop_reason"])

    async def test_repair_requires_approval_then_resumes(self):
        send = AsyncMock(
            side_effect=[
                json.dumps({"success": True, "email_id": 1}),
                json.dumps({"success": True, "email_id": 2}),
            ]
        )
        read = AsyncMock(
            side_effect=[persisted(1, body="Wrong"), persisted(2)]
        )
        policy = iter(
            [
                {"decision": ALLOW},
                {"decision": APPROVAL_REQUIRED, "reason": "Needs approval."},
                {"decision": ALLOW},
            ]
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call()]),
                AIMessage(content="Repair approved and verified."),
            ]
        )
        config = {"configurable": {"thread_id": "task18-approval"}}
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "evaluate_email", side_effect=lambda **_: next(policy)
        ), patch.object(graph_module, "call_send_email", send), patch.object(
            graph_module, "call_get_email", read
        ):
            paused = await graph_module.graph.ainvoke(state(), config=config)
            self.assertIn("__interrupt__", paused)
            result = await graph_module.graph.ainvoke(
                Command(resume="approve"), config=config
            )

        self.assertEqual("PASS", result["verification_result"]["status"])
        self.assertEqual(2, send.await_count)

    async def test_repair_limit_prevents_third_attempt(self):
        send = AsyncMock(
            side_effect=[
                json.dumps({"success": True, "email_id": 1}),
                json.dumps({"success": True, "email_id": 2}),
                json.dumps({"success": True, "email_id": 3}),
            ]
        )
        read = AsyncMock(
            side_effect=[
                persisted(1, body="Wrong"),
                persisted(2, body="Wrong"),
                persisted(3, body="Wrong"),
            ]
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call()]),
                AIMessage(content="Verification failed after repairs."),
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "evaluate_email", return_value={"decision": ALLOW}
        ), patch.object(graph_module, "call_send_email", send), patch.object(
            graph_module, "call_get_email", read
        ):
            result = await graph_module.graph.ainvoke(state())

        self.assertEqual("FAIL", result["verification_result"]["status"])
        self.assertEqual(
            "communication_verification_failed",
            result["execution_stop_reason"],
        )
        self.assertEqual(2, result["verification_repair_count"])
        self.assertEqual(3, send.await_count)

    async def test_missing_email_does_not_trigger_repair(self):
        send = AsyncMock(return_value=json.dumps({"success": True, "email_id": 1}))
        read = AsyncMock(
            return_value=json.dumps({"success": False, "error_type": "not_found"})
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call()]),
                AIMessage(content="The email could not be verified."),
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "evaluate_email", return_value={"decision": ALLOW}
        ), patch.object(graph_module, "call_send_email", send), patch.object(
            graph_module, "call_get_email", read
        ):
            result = await graph_module.graph.ainvoke(state())

        send.assert_awaited_once()
        self.assertEqual(0, result["verification_repair_count"])
        self.assertEqual("email_not_found", result["verification_result"]["reason"])


if __name__ == "__main__":
    unittest.main()
