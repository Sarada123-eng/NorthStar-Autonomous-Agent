import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from sqlalchemy import text

import app.agent.graph as graph_module
from app.agent.state import RunStatus
from app.database import engine, initialize_database


def initial_state(goal: str) -> dict:
    return {
        "run_id": "task11-test",
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

    async def ainvoke(self, messages):
        del messages
        return next(self.responses)


class TransientRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="northstar-task11-")
        self.state_file = Path(self.temp_dir.name) / "state.json"

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM customers WHERE name LIKE 'Task11%'")
            )
        if self.state_file.exists():
            self.state_file.unlink()
        self.temp_dir.cleanup()

    def create_call(self, name: str = "Task11NewCo") -> dict:
        return {
            "name": "create_customer",
            "args": {
                "name": name,
                "billing_contact": "billing@task11.example",
                "technical_contact": "tech@task11.example",
            },
            "type": "tool_call",
        }

    async def test_transient_failures_eventually_succeed_through_mcp(self):
        call = self.create_call()
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[{**call, "id": "call-1"}]),
                AIMessage(content="", tool_calls=[{**call, "id": "call-2"}]),
                AIMessage(content="", tool_calls=[{**call, "id": "call-3"}]),
                AIMessage(content="Task11NewCo was created successfully."),
            ]
        )

        with patch.dict(
            os.environ,
            {
                "NORTHSTAR_TRANSIENT_FAILURES": "create_customer:2",
                "NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE": str(self.state_file),
            },
        ), patch.object(graph_module, "get_llm", return_value=model):
            result = await graph_module.graph.ainvoke(
                initial_state("Create Task11NewCo with the supplied contacts.")
            )

        self.assertEqual(3, result["tool_step_count"])
        self.assertEqual("Task11NewCo was created successfully.", result["messages"][-1].content)
        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT COUNT(*) FROM customers WHERE name = 'Task11NewCo'")
                )
            ).scalar_one()
        self.assertEqual(1, count)

    async def test_transient_retry_exhaustion_stops_without_success(self):
        call = self.create_call("Task11Exhausted")
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[{**call, "id": "call-1"}]),
                AIMessage(content="", tool_calls=[{**call, "id": "call-2"}]),
                AIMessage(content="", tool_calls=[{**call, "id": "call-3"}]),
                AIMessage(content="", tool_calls=[{**call, "id": "call-4"}]),
                AIMessage(content="The operation could not be completed."),
            ]
        )

        with patch.dict(
            os.environ,
            {
                "NORTHSTAR_TRANSIENT_FAILURES": "create_customer:4",
                "NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE": str(self.state_file),
            },
        ), patch.object(graph_module, "get_llm", return_value=model):
            result = await graph_module.graph.ainvoke(
                initial_state("Create Task11Exhausted with the supplied contacts.")
            )

        self.assertEqual(4, result["tool_step_count"])
        self.assertEqual("transient_retry_exhausted", result["execution_stop_reason"])
        self.assertNotIn("successfully", result["messages"][-1].content.lower())
        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT COUNT(*) FROM customers WHERE name = 'Task11Exhausted'")
                )
            ).scalar_one()
        self.assertEqual(0, count)

    async def test_injection_result_is_structured_and_retryable(self):
        from app.agent.mcp_client import create_customer

        with patch.dict(
            os.environ,
            {
                "NORTHSTAR_TRANSIENT_FAILURES": "create_customer:1",
                "NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE": str(self.state_file),
            },
        ):
            result = json.loads(
                await create_customer(
                    "Task11Structured",
                    "billing@task11.example",
                    "tech@task11.example",
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("transient", result["error_type"])
        self.assertTrue(result["retryable"])


if __name__ == "__main__":
    unittest.main()
