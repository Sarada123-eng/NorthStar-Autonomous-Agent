import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from sqlalchemy import text

import app.agent.graph as graph_module
from app.agent.mcp_client import create_project, create_task, list_projects, list_tasks
from app.agent.state import RunStatus
from app.database import engine, initialize_database


def initial_state(goal: str) -> dict:
    return {
        "run_id": "task12-test",
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
        response = next(self.responses)
        if callable(response):
            return response(messages)
        return response


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    DELETE FROM tasks
                    WHERE project_id IN (
                        SELECT id FROM projects WHERE name LIKE 'Task12%'
                    )
                    """
                )
            )
            await connection.execute(
                text("DELETE FROM projects WHERE name LIKE 'Task12%'")
            )
        self.state_file = Path(tempfile.mkdtemp()) / "transient.json"

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    DELETE FROM tasks
                    WHERE project_id IN (
                        SELECT id FROM projects WHERE name LIKE 'Task12%'
                    )
                    """
                )
            )
            await connection.execute(
                text("DELETE FROM projects WHERE name LIKE 'Task12%'")
            )
        if self.state_file.exists():
            self.state_file.unlink()
        if self.state_file.parent.exists():
            self.state_file.parent.rmdir()

    def call(self, name: str, args: dict, identifier: str) -> dict:
        return {
            "name": name,
            "args": args,
            "id": identifier,
            "type": "tool_call",
        }

    async def test_multistep_onboarding_happy_path_uses_mcp(self):
        def project_id_from_messages(messages):
            for message in reversed(messages):
                if getattr(message, "type", None) == "tool":
                    payload = json.loads(message.content)
                    if "project" in payload:
                        return payload["project"]["id"]
            raise AssertionError("project result was not observed")

        def tasks_call(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "list_tasks",
                        {"project_id": project_id_from_messages(messages)},
                        "tasks",
                    )
                ],
            )

        def first_task_call(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "create_task",
                        {
                            "project_id": project_id_from_messages(messages),
                            "title": "Financial and contact verification",
                        },
                        "task-1",
                    )
                ],
            )

        def second_task_call(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "create_task",
                        {
                            "project_id": project_id_from_messages(messages),
                            "title": "Managed kickoff session",
                        },
                        "task-2",
                    )
                ],
            )

        def third_task_call(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "create_task",
                        {
                            "project_id": project_id_from_messages(messages),
                            "title": "Integration validation testing",
                        },
                        "task-3",
                    )
                ],
            )

        responses = [
            AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "search_documents",
                        {"query": "Globex onboarding requirements Business Tier"},
                        "search",
                    )
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "find_customer",
                        {"name": "Globex Corporation LLC"},
                        "find",
                    )
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "list_projects",
                        {"customer_name": "Globex Corporation LLC"},
                        "projects",
                    )
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    self.call(
                        "create_project",
                        {
                            "customer_name": "Globex Corporation LLC",
                            "name": "Task12 Globex Onboarding",
                        },
                        "create-project",
                    )
                ],
            ),
            tasks_call,
            first_task_call,
            second_task_call,
            third_task_call,
            AIMessage(
                content="Globex onboarding plan contains the required tasks."
            ),
        ]

        with patch.object(
            graph_module, "get_llm", return_value=FakeModel(responses)
        ):
            result = await graph_module.graph.ainvoke(
                initial_state("Onboard Globex Corporation LLC.")
            )

        self.assertEqual("Globex onboarding plan contains the required tasks.", result["messages"][-1].content)
        projects = json.loads(await list_projects("Globex Corporation LLC"))["projects"]
        project = next(p for p in projects if p["name"] == "Task12 Globex Onboarding")
        tasks = json.loads(await list_tasks(project["id"]))["tasks"]
        self.assertEqual(1, len([p for p in projects if p["name"] == "Task12 Globex Onboarding"]))
        self.assertEqual(3, len(tasks))

    async def test_existing_project_and_tasks_are_not_recreated(self):
        project = json.loads(
            await create_project("Globex Corporation LLC", "Task12 Existing Onboarding")
        )["project"]
        await create_task(project["id"], "Financial and contact verification")

        model = FakeModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        self.call(
                            "find_customer",
                            {"name": "Globex Corporation LLC"},
                            "find",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        self.call(
                            "list_projects",
                            {"customer_name": "Globex Corporation LLC"},
                            "projects",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        self.call(
                            "list_tasks",
                            {"project_id": project["id"]},
                            "tasks",
                        )
                    ],
                ),
                AIMessage(content="Existing onboarding state requires no duplicate artifacts."),
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model):
            result = await graph_module.graph.ainvoke(
                initial_state("Onboard Globex Corporation LLC.")
            )

        self.assertIn("no duplicate", result["messages"][-1].content)
        tasks = json.loads(await list_tasks(project["id"]))["tasks"]
        self.assertEqual(1, len(tasks))

    async def test_transient_failure_during_task_creation_recovers(self):
        project = json.loads(
            await create_project("Globex Corporation LLC", "Task12 Transient Onboarding")
        )["project"]
        task_call = self.call(
            "create_task",
            {"project_id": project["id"], "title": "Task12 transient task"},
            "task-1",
        )
        model = FakeModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        self.call(
                            "list_projects",
                            {"customer_name": "Globex Corporation LLC"},
                            "projects",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        self.call("list_tasks", {"project_id": project["id"]}, "inspect")
                    ],
                ),
                AIMessage(content="", tool_calls=[task_call]),
                AIMessage(content="", tool_calls=[{**task_call, "id": "task-2"}]),
                AIMessage(content="The transient failure recovered and the task was created."),
            ]
        )
        with patch.dict(
            os.environ,
            {
                "NORTHSTAR_TRANSIENT_FAILURES": "create_task:1",
                "NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE": str(self.state_file),
            },
        ), patch.object(graph_module, "get_llm", return_value=model):
            result = await graph_module.graph.ainvoke(
                initial_state("Onboard Globex Corporation LLC.")
            )

        self.assertEqual(4, result["tool_step_count"])
        tasks = json.loads(await list_tasks(project["id"]))["tasks"]
        self.assertEqual(1, len(tasks))


if __name__ == "__main__":
    unittest.main()
