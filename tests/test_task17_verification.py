import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage

import app.agent.graph as graph_module
from app.agent.state import RunStatus
from app.database import engine, initialize_database
from app.policy.communication import ALLOW


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
        "pending_approval_action": None,
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
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        return next(self.responses)


def email_call():
    return {
        "name": "send_email",
        "args": {
            "to": "verified@acmecorp.com",
            "subject": "Task 17 verification",
            "body": "The persisted message must match this body.",
        },
        "id": "task17-email",
        "type": "tool_call",
    }


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "DELETE FROM emails WHERE subject = 'Task 17 verification'"
            )

    def model(self):
        return FakeModel(
            [
                AIMessage(content="", tool_calls=[email_call()]),
                AIMessage(content="The mock email was recorded and verified."),
            ]
        )

    async def test_successful_send_is_verified_against_outbox(self):
        with patch.object(graph_module, "get_llm", return_value=self.model()), patch.object(
            graph_module,
            "evaluate_email",
            return_value={"decision": ALLOW, "reason": "verified contact"},
        ):
            result = await graph_module.graph.ainvoke(
                initial_state("Send and verify the project update.")
            )

        self.assertEqual("PASS", result["verification_result"]["status"])
        self.assertIsNone(result["execution_stop_reason"])
        self.assertEqual(
            "The mock email was recorded and verified.",
            result["messages"][-1].content,
        )

    async def test_mismatch_and_missing_email_fail_without_repair(self):
        cases = [
            (
                "recipient",
                {"recipient": "different@example.com"},
            ),
            (
                "subject",
                {"subject": "Unexpected subject"},
            ),
            (
                "body",
                {"body": "Unexpected body"},
            ),
        ]
        for field, override in cases:
            with self.subTest(field=field):
                persisted = {
                    "success": True,
                    "email": {
                        "recipient": email_call()["args"]["to"],
                        "subject": email_call()["args"]["subject"],
                        "body": email_call()["args"]["body"],
                        "status": "sent",
                        **override,
                    },
                }
                with patch.object(
                    graph_module, "get_llm", return_value=self.model()
                ), patch.object(
                    graph_module,
                    "evaluate_email",
                    return_value={"decision": ALLOW, "reason": "verified contact"},
                ), patch.object(
                    graph_module,
                    "call_get_email",
                    return_value=json.dumps(persisted),
                ):
                    result = await graph_module.graph.ainvoke(
                        initial_state("Send and verify the project update.")
                    )

                self.assertEqual("FAIL", result["verification_result"]["status"])
                self.assertEqual(
                    "communication_verification_failed",
                    result["execution_stop_reason"],
                )
                self.assertEqual(field, result["verification_result"]["reason"])

        with patch.object(graph_module, "get_llm", return_value=self.model()), patch.object(
            graph_module,
            "evaluate_email",
            return_value={"decision": ALLOW, "reason": "verified contact"},
        ), patch.object(
            graph_module,
            "call_get_email",
            return_value=json.dumps(
                {"success": False, "error_type": "not_found"}
            ),
        ):
            result = await graph_module.graph.ainvoke(
                initial_state("Send and verify the project update.")
            )

        self.assertEqual("email_not_found", result["verification_result"]["reason"])
        self.assertEqual(
            "communication_verification_failed",
            result["execution_stop_reason"],
        )


if __name__ == "__main__":
    unittest.main()
