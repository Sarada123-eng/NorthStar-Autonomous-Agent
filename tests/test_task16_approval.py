import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy import text

import app.agent.graph as graph_module
from app.agent.mcp_client import get_email
from app.agent.state import RunStatus
from app.database import engine, initialize_database
from app.policy.communication import ALLOW, APPROVAL_REQUIRED, DENY


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


def send_call():
    return {
        "name": "send_email",
        "args": {
            "to": "partner@consultant.example",
            "subject": "Project onboarding information",
            "body": "Kickoff is scheduled.",
        },
        "id": "approval-email",
        "type": "tool_call",
    }


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM emails WHERE subject = 'Project onboarding information'")
            )

    async def test_approval_required_interrupts_without_mcp(self):
        model = FakeModel(
            [AIMessage(content="", tool_calls=[send_call()])]
        )
        send = AsyncMock()
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_send_email", send
        ):
            paused = await graph_module.graph.ainvoke(
                initial_state("Send project information to the partner.")
            )

        self.assertIn("__interrupt__", paused)
        self.assertEqual("approval", paused["__interrupt__"][0].value["type"])
        self.assertEqual("send_email", paused["__interrupt__"][0].value["tool"])
        self.assertEqual(send_call()["args"], paused["__interrupt__"][0].value["arguments"])
        send.assert_not_awaited()

    async def test_approve_rechecks_then_executes_exact_action(self):
        call = send_call()
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call]),
                AIMessage(content="Approved email was recorded."),
            ]
        )
        decisions = iter(
            [
                {"decision": APPROVAL_REQUIRED, "reason": "Approval required."},
                {"decision": ALLOW, "reason": "Current context now allows it."},
            ]
        )
        config = {"configurable": {"thread_id": "task16-approve"}}
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "evaluate_email", side_effect=lambda **_: next(decisions)
        ):
            paused = await graph_module.graph.ainvoke(
                initial_state("Send project information to the partner."), config=config
            )
            self.assertIn("__interrupt__", paused)
            result = await graph_module.graph.ainvoke(
                Command(resume="approve"), config=config
            )

        self.assertEqual("Approved email was recorded.", result["messages"][-1].content)
        self.assertIsNone(result["pending_approval_action"])
        email_id = json.loads(result["last_tool_result"]["tools"][0]["content"])[
            "email_id"
        ]
        persisted = json.loads(await get_email(email_id))
        self.assertEqual(call["args"]["to"], persisted["email"]["recipient"])

    async def test_reject_does_not_execute(self):
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[send_call()]),
                AIMessage(content="The email was rejected."),
            ]
        )
        send = AsyncMock()
        config = {"configurable": {"thread_id": "task16-reject"}}
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_send_email", send
        ):
            await graph_module.graph.ainvoke(
                initial_state("Send project information to the partner."), config=config
            )
            result = await graph_module.graph.ainvoke(
                Command(resume="reject"), config=config
            )

        send.assert_not_awaited()
        self.assertEqual("communication_approval_rejected", result["execution_stop_reason"])

    async def test_approval_cannot_bypass_deny_or_remaining_approval(self):
        for decision, stop_reason in [
            (DENY, "communication_denied"),
        ]:
            with self.subTest(decision=decision):
                model = FakeModel(
                    [
                        AIMessage(content="", tool_calls=[send_call()]),
                        AIMessage(content="The email remains blocked."),
                    ]
                )
                send = AsyncMock()
                config = {"configurable": {"thread_id": f"task16-{decision}"}}
                with patch.object(graph_module, "get_llm", return_value=model), patch.object(
                    graph_module, "call_send_email", send
                ), patch.object(
                    graph_module,
                    "evaluate_email",
                    return_value={
                        "decision": decision,
                        "reason": "Policy still blocks this action.",
                    },
                ):
                    await graph_module.graph.ainvoke(
                        initial_state("Send project information to the partner."),
                        config=config,
                    )
                    result = await graph_module.graph.ainvoke(
                        Command(resume="approve"), config=config
                    )

                send.assert_not_awaited()
                self.assertEqual(stop_reason, result["execution_stop_reason"])

    async def test_approval_allows_exact_action_when_policy_still_requires_approval(self):
        call = send_call()
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call]),
                AIMessage(content="The approved email was sent."),
            ]
        )
        send = AsyncMock(
            return_value=json.dumps({"success": True, "email_id": 901})
        )
        get = AsyncMock(
            return_value=json.dumps(
                {
                    "success": True,
                    "email": {
                        "recipient": call["args"]["to"],
                        "subject": call["args"]["subject"],
                        "body": call["args"]["body"],
                        "status": "sent",
                    },
                }
            )
        )
        config = {"configurable": {"thread_id": "task16-approved-approval"}}
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_send_email", send
        ), patch.object(graph_module, "call_get_email", get), patch.object(
            graph_module,
            "evaluate_email",
            return_value={
                "decision": APPROVAL_REQUIRED,
                "reason": "Approval is required for this recipient.",
            },
        ):
            paused = await graph_module.graph.ainvoke(
                initial_state("Send project information to the partner."), config=config
            )
            result = await graph_module.graph.ainvoke(
                Command(resume="approve"), config=config
            )

        send.assert_awaited_once()
        self.assertEqual("The approved email was sent.", result["messages"][-1].content)


if __name__ == "__main__":
    unittest.main()
