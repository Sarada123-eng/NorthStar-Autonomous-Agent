import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

import app.agent.graph as graph_module
from app.agent.state import RunStatus
from app.database import create_task as database_create_task


def initial_state(goal: str) -> dict:
    run_id = str(uuid4())
    return {
        "run_id": run_id,
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
        "customer_resolution": {
            "status": "resolved",
            "reference": "Acme Corporation Inc.",
            "run_id": run_id,
            "normalized_reference": "acme corporation inc",
            "lookup_result": {"found": True, "customer": {"id": 1}},
        },
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


def call(name: str, args: dict, identifier: str) -> dict:
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, _messages):
        return next(self.responses)


class ToolErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_rejects_unknown_project_with_structured_result(self):
        result = await database_create_task(999999999, "Record invoice")

        self.assertFalse(result["success"])
        self.assertEqual("invalid_project_id", result["error_type"])
        self.assertEqual("foreign_key_violation", result["error"]["type"])
        self.assertFalse(result["retryable"])

    async def test_tool_exception_becomes_structured_non_retryable_result(self):
        model = FakeModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[call("list_tasks", {"project_id": 1}, "inspect-bad")],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        call(
                            "create_task",
                            {"project_id": 1, "title": "Record invoice"},
                            "bad-task",
                        )
                    ],
                ),
                AIMessage(content="The task could not be created."),
            ]
        )
        create_task = AsyncMock(
            side_effect=RuntimeError("Foreign key violation for project_id 1")
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_task", create_task
        ), patch.object(graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": []}))):
            result = await graph_module.graph.ainvoke(
                initial_state("Record the latest invoice in the project.")
            )

        observation = next(
            json.loads(message.content)
            for message in result["messages"]
            if isinstance(message, ToolMessage)
            and json.loads(message.content).get("error_type") == "invalid_project_id"
        )
        self.assertFalse(observation["success"])
        self.assertEqual("invalid_project_id", observation["error_type"])
        self.assertFalse(observation["retryable"])
        self.assertEqual(2, result["tool_step_count"])
        create_task.assert_awaited_once_with(1, "Record invoice")

    async def test_invalid_project_id_can_be_replanned_without_blind_retry(self):
        model = FakeModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[call("list_tasks", {"project_id": 1}, "inspect-bad")],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        call(
                            "create_task",
                            {"project_id": 1, "title": "Record invoice"},
                            "bad-task",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        call(
                            "list_projects",
                            {"customer_name": "Acme Corporation Inc."},
                            "projects",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[call("list_tasks", {"project_id": 61}, "inspect-good")],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        call(
                            "create_task",
                            {"project_id": 61, "title": "Record invoice"},
                            "good-task",
                        )
                    ],
                ),
                AIMessage(content="Invoice details were logged."),
            ]
        )
        create_task = AsyncMock(
            side_effect=[
                RuntimeError("Foreign key violation for project_id 1"),
                json.dumps(
                    {
                        "success": True,
                        "task": {
                            "id": 99,
                            "project_id": 61,
                            "title": "Record invoice",
                        },
                    }
                ),
            ]
        )
        list_projects = AsyncMock(
            return_value=json.dumps(
                {
                    "success": True,
                    "projects": [
                        {
                            "id": 61,
                            "customer_id": 1,
                            "name": "Acme Onboarding",
                        }
                    ],
                }
            )
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_task", create_task
        ), patch.object(graph_module, "call_list_projects", list_projects), patch.object(
            graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": []}))
        ):
            result = await graph_module.graph.ainvoke(
                initial_state("Log the latest invoice for Acme Corporation Inc.")
            )

        self.assertEqual("Invoice details were logged.", result["messages"][-1].content)
        self.assertEqual(5, result["tool_step_count"])
        create_task.assert_any_await(1, "Record invoice")
        create_task.assert_any_await(61, "Record invoice")
        self.assertEqual(2, create_task.await_count)
        list_projects.assert_awaited_once_with("Acme Corporation Inc.")


if __name__ == "__main__":
    unittest.main()
