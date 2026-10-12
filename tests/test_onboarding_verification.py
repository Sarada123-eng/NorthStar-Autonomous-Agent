import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

import app.agent.graph as graph_module
from app.agent.state import RunStatus


def initial_state(goal: str = "Create a project for Acme Corporation Inc.") -> dict:
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
            "lookup_result": {
                "found": True,
                "customer": {"id": 1, "legal_name": "Acme Corporation Inc."},
            },
            "customer": {"id": 1, "legal_name": "Acme Corporation Inc."},
        },
        "verification_result": None,
        "onboarding_verification_result": None,
        "invoice_verification_result": None,
        "verification_repair_count": 0,
        "tool_step_count": 0,
        "retry_counts": {},
        "last_action_signature": None,
        "consecutive_action_count": 0,
        "last_failed_non_retryable_action": None,
        "execution_stop_reason": None,
        "error": None,
    }


def tool_call(name: str, args: dict, identifier: str) -> dict:
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


def state_with_observed_project() -> dict:
    state = initial_state()
    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[tool_call("list_projects", {"customer_name": "Acme Corporation Inc."}, "projects")],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "success": True,
                    "projects": [
                        {"id": 20, "customer_id": 1, "name": "Verified Project", "status": "onboarding"}
                    ],
                }
            ),
            tool_call_id="projects",
        ),
        AIMessage(
            content="",
            tool_calls=[tool_call("list_tasks", {"project_id": 20}, "tasks")],
        ),
        ToolMessage(
            content=json.dumps({"success": True, "tasks": []}),
            tool_call_id="tasks",
        ),
    ]
    return state


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen_messages = []

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.seen_messages.append(messages)
        return next(self.responses)


class OnboardingWriteVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_batch_verifies_successful_project_before_resuming_planning(self):
        project_action = tool_call(
            "create_project",
            {"customer_name": "Acme Corporation Inc.", "name": "Mixed Project"},
            "project",
        )
        failed_task_action = tool_call(
            "create_task", {"project_id": 20, "title": "Existing task"}, "task"
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[project_action, failed_task_action]),
                AIMessage(
                    content="The project was created and verified. The task was not created."
                ),
            ]
        )
        created_project = {
            "id": 21,
            "customer_id": 1,
            "name": "Mixed Project",
            "status": "onboarding",
        }
        duplicate = json.dumps(
            {
                "success": False,
                "error_type": "duplicate",
                "retryable": False,
                "message": "Task already exists.",
            }
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_create_project",
            AsyncMock(return_value=json.dumps({"success": True, "project": created_project})),
        ) as create_project, patch.object(
            graph_module, "call_create_task", AsyncMock(return_value=duplicate)
        ) as create_task, patch.object(
            graph_module,
            "call_list_projects",
            AsyncMock(return_value=json.dumps({"success": True, "projects": [created_project]})),
        ) as list_projects:
            result = await graph_module.graph.ainvoke(state_with_observed_project())

        create_project.assert_awaited_once_with("Acme Corporation Inc.", "Mixed Project")
        create_task.assert_awaited_once_with(20, "Existing task")
        list_projects.assert_awaited_once_with("Acme Corporation Inc.")
        self.assertEqual("PASS", result["onboarding_verification_result"]["status"])
        self.assertEqual(
            [{"status": "PASS", "kind": "project", "project_id": 21}],
            result["onboarding_verification_result"]["writes"],
        )
        self.assertEqual(2, len(model.seen_messages))
        self.assertEqual(
            "duplicate",
            graph_module.parse_tool_result(result["messages"][-2].content)["error_type"],
        )
        self.assertTrue(
            any(
                isinstance(message, SystemMessage)
                and "only verified newly created project IDs are [21]" in message.content
                and "only verified newly created task IDs are []" in message.content
                for message in model.seen_messages[-1]
            )
        )
        self.assertEqual(
            "The project was created and verified. The task was not created.",
            result["messages"][-1].content,
        )

    async def test_mixed_batch_verifies_successful_task_before_resuming_planning(self):
        successful_task_action = tool_call(
            "create_task", {"project_id": 20, "title": "Verified mixed task"}, "task-ok"
        )
        failed_task_action = tool_call(
            "create_task", {"project_id": 20, "title": "Existing task"}, "task-failed"
        )
        model = FakeModel(
            [
                AIMessage(
                    content="", tool_calls=[successful_task_action, failed_task_action]
                ),
                AIMessage(
                    content="The verified mixed task was created. The other task was not created."
                ),
            ]
        )
        project = {
            "id": 20,
            "customer_id": 1,
            "name": "Verified Project",
            "status": "onboarding",
        }
        created_task = {
            "id": 30,
            "project_id": 20,
            "title": "Verified mixed task",
            "status": "open",
        }
        duplicate = json.dumps(
            {
                "success": False,
                "error_type": "duplicate",
                "retryable": False,
                "message": "Task already exists.",
            }
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_create_task",
            AsyncMock(
                side_effect=[
                    json.dumps({"success": True, "task": created_task}),
                    duplicate,
                ]
            ),
        ) as create_task, patch.object(
            graph_module,
            "call_list_projects",
            AsyncMock(return_value=json.dumps({"success": True, "projects": [project]})),
        ), patch.object(
            graph_module,
            "call_list_tasks",
            AsyncMock(return_value=json.dumps({"success": True, "tasks": [created_task]})),
        ) as list_tasks:
            result = await graph_module.graph.ainvoke(state_with_observed_project())

        self.assertEqual(2, create_task.await_count)
        list_tasks.assert_awaited_once_with(20)
        self.assertEqual("PASS", result["onboarding_verification_result"]["status"])
        self.assertEqual(
            [{"status": "PASS", "kind": "task", "task_id": 30, "project_id": 20}],
            result["onboarding_verification_result"]["writes"],
        )
        self.assertEqual(2, len(model.seen_messages))
        self.assertEqual(
            "duplicate",
            graph_module.parse_tool_result(result["messages"][-2].content)["error_type"],
        )
        self.assertTrue(
            any(
                isinstance(message, SystemMessage)
                and "only verified newly created project IDs are []" in message.content
                and "only verified newly created task IDs are [30]" in message.content
                for message in model.seen_messages[-1]
            )
        )

    async def test_correct_project_read_back_passes_and_continues_planning(self):
        action = tool_call(
            "create_project",
            {"customer_name": "Acme Corporation Inc.", "name": "Verified Project"},
            "project",
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[action]),
                AIMessage(content="The project was created and read back."),
            ]
        )
        created = {"id": 20, "customer_id": 1, "name": "Verified Project", "status": "onboarding"}
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_project", AsyncMock(return_value=json.dumps({"success": True, "project": created}))
        ) as create_project, patch.object(
            graph_module, "call_list_projects", AsyncMock(return_value=json.dumps({"success": True, "projects": [created]}))
        ) as list_projects:
            result = await graph_module.graph.ainvoke(state_with_observed_project())

        create_project.assert_awaited_once_with("Acme Corporation Inc.", "Verified Project")
        list_projects.assert_awaited_once_with("Acme Corporation Inc.")
        self.assertEqual("PASS", result["onboarding_verification_result"]["status"])
        self.assertIsNone(result["execution_stop_reason"])
        self.assertEqual("The project was created and read back.", result["messages"][-1].content)

    async def test_correct_task_read_back_passes(self):
        action = tool_call(
            "create_task", {"project_id": 20, "title": "Verified task"}, "task"
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[action]),
                AIMessage(content="The task was created and read back."),
            ]
        )
        project = {"id": 20, "customer_id": 1, "name": "Verified Project", "status": "onboarding"}
        created = {"id": 30, "project_id": 20, "title": "Verified task", "status": "open"}
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_task", AsyncMock(return_value=json.dumps({"success": True, "task": created}))
        ) as create_task, patch.object(
            graph_module, "call_list_projects", AsyncMock(return_value=json.dumps({"success": True, "projects": [project]}))
        ), patch.object(
            graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": [created]}))
        ) as list_tasks:
            result = await graph_module.graph.ainvoke(state_with_observed_project())

        create_task.assert_awaited_once_with(20, "Verified task")
        list_tasks.assert_awaited_once_with(20)
        self.assertEqual("PASS", result["onboarding_verification_result"]["status"])
        self.assertIsNone(result["execution_stop_reason"])

    async def test_missing_project_or_task_stops_without_duplicate_write(self):
        for kind in ("project", "task"):
            with self.subTest(kind=kind):
                if kind == "project":
                    action = tool_call(
                        "create_project",
                        {"customer_name": "Acme Corporation Inc.", "name": "Missing Project"},
                        "project",
                    )
                    create_patch = patch.object(
                        graph_module,
                        "call_create_project",
                        AsyncMock(return_value=json.dumps({"success": True, "project": {"id": 21, "customer_id": 1, "name": "Missing Project", "status": "onboarding"}})),
                    )
                    reads = [patch.object(graph_module, "call_list_projects", AsyncMock(return_value=json.dumps({"success": True, "projects": []})))]
                else:
                    action = tool_call("create_task", {"project_id": 20, "title": "Missing task"}, "task")
                    create_patch = patch.object(
                        graph_module,
                        "call_create_task",
                        AsyncMock(return_value=json.dumps({"success": True, "task": {"id": 31, "project_id": 20, "title": "Missing task", "status": "open"}})),
                    )
                    reads = [
                        patch.object(graph_module, "call_list_projects", AsyncMock(return_value=json.dumps({"success": True, "projects": [{"id": 20, "customer_id": 1}]}))),
                        patch.object(graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": []}))),
                    ]
                model = FakeModel([AIMessage(content="", tool_calls=[action]), AIMessage(content="Verification stopped safely.")])
                with patch.object(graph_module, "get_llm", return_value=model), create_patch as create, reads[0]:
                    if len(reads) == 2:
                        with reads[1]:
                            result = await graph_module.graph.ainvoke(state_with_observed_project())
                    else:
                        result = await graph_module.graph.ainvoke(
                            state_with_observed_project() if kind == "task" else initial_state()
                        )

                create.assert_awaited_once()
                self.assertEqual("FAIL", result["onboarding_verification_result"]["status"])
                self.assertEqual("onboarding_verification_failed", result["execution_stop_reason"])
                self.assertEqual("Verification stopped safely.", result["messages"][-1].content)
                self.assertTrue(
                    any(
                        isinstance(message, SystemMessage)
                        and "Do not claim that the requested operation completed" in message.content
                        for message in model.seen_messages[-1]
                    )
                )

    async def test_wrong_relationship_name_title_or_status_fails_safely(self):
        cases = [
            ("project_customer", "create_project", {"id": 20, "customer_id": 2, "name": "Verified Project", "status": "onboarding"}, [{"id": 20, "customer_id": 2, "name": "Verified Project", "status": "onboarding"}]),
            ("project_name", "create_project", {"id": 20, "customer_id": 1, "name": "Verified Project", "status": "onboarding"}, [{"id": 20, "customer_id": 1, "name": "Wrong", "status": "onboarding"}]),
            ("project_status", "create_project", {"id": 20, "customer_id": 1, "name": "Verified Project", "status": "onboarding"}, [{"id": 20, "customer_id": 1, "name": "Verified Project", "status": "closed"}]),
            ("task_project", "create_task", {"id": 30, "project_id": 20, "title": "Verified task", "status": "open"}, [{"id": 20, "customer_id": 2}]),
            ("task_title", "create_task", {"id": 30, "project_id": 20, "title": "Verified task", "status": "open"}, [{"id": 20, "customer_id": 1}]),
            ("task_status", "create_task", {"id": 30, "project_id": 20, "title": "Verified task", "status": "open"}, [{"id": 20, "customer_id": 1}]),
        ]
        for case, tool_name, created, projects in cases:
            with self.subTest(case=case):
                if tool_name == "create_project":
                    action = tool_call(tool_name, {"customer_name": "Acme Corporation Inc.", "name": "Verified Project"}, case)
                    create_patch = patch.object(graph_module, "call_create_project", AsyncMock(return_value=json.dumps({"success": True, "project": created})))
                    read_patches = [patch.object(graph_module, "call_list_projects", AsyncMock(return_value=json.dumps({"success": True, "projects": projects})))]
                else:
                    action = tool_call(tool_name, {"project_id": created["project_id"], "title": "Verified task"}, case)
                    create_patch = patch.object(graph_module, "call_create_task", AsyncMock(return_value=json.dumps({"success": True, "task": created})))
                    task = {"id": 30, "project_id": created["project_id"], "title": "Verified task", "status": "open"}
                    if case == "task_title":
                        task["title"] = "Wrong task"
                    if case == "task_status":
                        task["status"] = "closed"
                    read_patches = [
                        patch.object(graph_module, "call_list_projects", AsyncMock(return_value=json.dumps({"success": True, "projects": projects}))),
                        patch.object(graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": [task]}))),
                    ]
                model = FakeModel([AIMessage(content="", tool_calls=[action]), AIMessage(content="Stopped.")])
                with patch.object(graph_module, "get_llm", return_value=model), create_patch, read_patches[0]:
                    if len(read_patches) == 2:
                        with read_patches[1]:
                            result = await graph_module.graph.ainvoke(
                                state_with_observed_project() if tool_name == "create_task" else initial_state()
                            )
                    else:
                        result = await graph_module.graph.ainvoke(
                            state_with_observed_project() if tool_name == "create_task" else initial_state()
                        )
                self.assertEqual("FAIL", result["onboarding_verification_result"]["status"])
                self.assertEqual("onboarding_verification_failed", result["execution_stop_reason"])


if __name__ == "__main__":
    unittest.main()
