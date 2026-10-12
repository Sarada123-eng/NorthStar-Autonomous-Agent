import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from sqlalchemy import text

import app.agent.graph as graph_module
from app.agent.mcp_client import get_email
from app.agent.state import RunStatus
from app.database import engine, initialize_database
from app.policy.communication import ALLOW, APPROVAL_REQUIRED, DENY, evaluate_email


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
        del tools
        return self

    async def ainvoke(self, _messages):
        del _messages
        return next(self.responses)


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM emails WHERE subject = 'Task 15 integration'")
            )

    async def test_policy_decisions(self):
        customer = {
            "billing_contact": "Daniel Okafor <ap-invoicing@acmecorp.com>",
            "technical_contact": "Marcus Brody <m.brody@acmecorp.com>",
        }
        self.assertEqual(
            ALLOW,
            evaluate_email(
                recipient="m.brody@acmecorp.com",
                subject="Onboarding update",
                body="Kickoff is scheduled.",
                customer=customer,
            )["decision"],
        )
        self.assertEqual(
            APPROVAL_REQUIRED,
            evaluate_email(
                recipient="partner@consultant.example",
                subject="Onboarding update",
                body="Kickoff is scheduled.",
                customer=customer,
            )["decision"],
        )
        self.assertEqual(
            DENY,
            evaluate_email(
                recipient="m.brody@acmecorp.com",
                subject="Credentials",
                body="Here is the API key: fake-test-key.",
                customer=customer,
            )["decision"],
        )
        self.assertEqual(
            ALLOW,
            evaluate_email(
                recipient="sarada161123@gmail.com",
                subject="Northstar live LLM test",
                body="This is a controlled test from the Northstar Autonomy Engine.",
                customer={
                    "billing_contact": "Daniel Okafor <ap-invoicing@acmecorp.com>",
                    "technical_contact": (
                        "Marcus Brody <m.brody@acmecorp.com>; "
                        "Sarada <sarada161123@gmail.com>"
                    ),
                },
            )["decision"],
        )

    async def test_allow_reaches_communication_mcp(self):
        call = {
            "name": "send_email",
            "args": {
                "to": "m.brody@acmecorp.com",
                "subject": "Onboarding update",
                "body": "Kickoff is scheduled.",
            },
            "id": "email-allow",
            "type": "tool_call",
        }
        customer = {
            "billing_contact": "Daniel Okafor <ap-invoicing@acmecorp.com>",
            "technical_contact": "Marcus Brody <m.brody@acmecorp.com>",
        }
        model = FakeModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "find_customer",
                            "args": {"name": "Acme"},
                            "id": "find",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="", tool_calls=[call]),
                AIMessage(content="Email was recorded."),
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_find_customer",
            return_value=json.dumps({"found": True, "customer": customer}),
        ), patch.object(
            graph_module,
            "call_send_email",
            return_value=json.dumps({"success": True, "email": {"id": 1}}),
        ) as send:
            result = await graph_module.graph.ainvoke(
                initial_state("Send an onboarding update to Acme.")
            )
        send.assert_awaited_once()
        self.assertEqual("Email was recorded.", result["messages"][-1].content)

    async def test_allowed_email_uses_real_mcp_and_postgres(self):
        model = FakeModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "find_customer",
                            "args": {"name": "Acme"},
                            "id": "find-real",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "send_email",
                            "args": {
                                "to": "m.brody@acmecorp.com",
                                "subject": "Task 15 integration",
                                "body": "Kickoff is scheduled.",
                            },
                            "id": "email-real",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="The approved mock email was recorded."),
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model):
            result = await graph_module.graph.ainvoke(
                initial_state("Send an onboarding update to Acme.")
            )

        self.assertEqual(
            "The approved mock email was recorded.",
            result["messages"][-1].content,
        )
        email_id = json.loads(
            result["last_tool_result"]["tools"][0]["content"]
        )["email"]["id"]
        stored = json.loads(await get_email(email_id))
        self.assertEqual("Task 15 integration", stored["email"]["subject"])

    async def test_approval_and_denial_never_reach_mcp(self):
        for body, decision, stop_reason in [
            ("Kickoff is scheduled.", APPROVAL_REQUIRED, "communication_approval_required"),
            ("Here is the API key: fake-test-key.", DENY, "communication_denied"),
        ]:
            with self.subTest(decision=decision):
                call = {
                    "name": "send_email",
                    "args": {
                        "to": "outside@consultant.example",
                        "subject": "Onboarding",
                        "body": body,
                    },
                    "id": "blocked-email",
                    "type": "tool_call",
                }
                model = FakeModel(
                    [
                        AIMessage(content="", tool_calls=[call]),
                        AIMessage(content="The email was not sent."),
                    ]
                )
                with patch.object(
                    graph_module, "get_llm", return_value=model
                ), patch.object(graph_module, "call_send_email") as send:
                    result = await graph_module.graph.ainvoke(
                        initial_state("Send this email.")
                    )
                send.assert_not_awaited()
                payload = json.loads(result["last_tool_result"]["tools"][0]["content"])
                self.assertEqual(decision, payload["policy_decision"])
                self.assertEqual(stop_reason, result["execution_stop_reason"])


if __name__ == "__main__":
    unittest.main()
