import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.types import Command

import app.agent.graph as graph_module
from app.agent.state import RunStatus


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
        "customer_resolution": None,
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
        self.calls = 0

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        return next(self.responses)


class CustomerResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolved_lookup_is_reused_for_project_listing(self):
        find = call("find_customer", {"name": "Delta Health"}, "find")
        projects = call(
            "list_projects",
            {"customer_name": "Delta Health"},
            "projects",
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[find]),
                AIMessage(content="", tool_calls=[projects]),
                AIMessage(content="Delta Health projects listed."),
            ]
        )
        find_customer = AsyncMock(
            return_value=json.dumps(
                {
                    "found": True,
                    "resolution": {
                        "status": "resolved",
                        "reference": "Delta Health",
                    },
                    "customer": {
                        "id": 3,
                        "name": "Delta Health",
                        "legal_name": "Delta Health",
                    },
                }
            )
        )
        list_projects = AsyncMock(
            return_value=json.dumps(
                {
                    "success": True,
                    "projects": [{"id": 72, "customer_id": 3, "name": "Onboarding"}],
                }
            )
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_find_customer", find_customer
        ), patch.object(graph_module, "call_list_projects", list_projects):
            result = await graph_module.graph.ainvoke(
                initial_state("Onboard Delta Health")
            )

        find_customer.assert_awaited_once_with("Delta Health")
        list_projects.assert_awaited_once_with("Delta Health")
        self.assertEqual("resolved", result["customer_resolution"]["status"])
        self.assertEqual(3, result["customer_resolution"]["customer"]["id"])
        self.assertEqual("Delta Health projects listed.", result["messages"][-1].content)
        self.assertNotIn("__interrupt__", result)

    async def test_unsupported_customer_alias_does_not_reuse_resolved_identity(self):
        projects = call(
            "list_projects",
            {"customer_name": "Delta Health Services, LLC"},
            "projects",
        )
        model = FakeModel([AIMessage(content="", tool_calls=[projects])])
        list_projects = AsyncMock()
        state = initial_state("Onboard Delta Health")
        state["customer_resolution"] = {
            "status": "resolved",
            "reference": "Delta Health",
            "run_id": state["run_id"],
            "normalized_reference": "delta health",
            "customer": {
                "id": 3,
                "name": "Delta Health",
                "legal_name": "Delta Health",
            },
        }

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_list_projects", list_projects
        ):
            paused = await graph_module.graph.ainvoke(state)

        self.assertIn("__interrupt__", paused)
        list_projects.assert_not_awaited()

    async def test_explicit_customer_resolves_before_customer_scoped_read(self):
        list_call = call("list_tasks", {"project_id": 61}, "tasks")
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[list_call]),
                AIMessage(content="The project has one task."),
            ]
        )
        find_customer = AsyncMock(
            return_value=json.dumps(
                {
                    "found": True,
                    "resolution": {
                        "status": "resolved",
                        "reference": "Acme Corporation Inc.",
                    },
                    "customer": {"id": 1, "legal_name": "Acme Corporation Inc."},
                }
            )
        )
        list_tasks = AsyncMock(
            return_value=json.dumps({"success": True, "tasks": [{"id": 98}]})
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_find_customer", find_customer
        ), patch.object(graph_module, "call_list_tasks", list_tasks):
            result = await graph_module.graph.ainvoke(
                initial_state(
                    "Find all tasks for the Acme Onboarding project for "
                    "Acme Corporation Inc."
                )
            )

        find_customer.assert_awaited_once_with("Acme Corporation Inc")
        list_tasks.assert_awaited_once_with(61)
        self.assertNotIn("__interrupt__", result)
        self.assertEqual("resolved", result["customer_resolution"]["status"])

    async def test_explicit_customer_resolves_before_customer_scoped_write(self):
        create_call = call(
            "create_project",
            {"customer_name": "Acme Corporation Inc.", "name": "New Onboarding"},
            "project",
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[create_call]),
                AIMessage(content="The project was created."),
            ]
        )
        find_customer = AsyncMock(
            return_value=json.dumps(
                {
                    "found": True,
                    "resolution": {"status": "resolved"},
                    "customer": {"id": 1, "legal_name": "Acme Corporation Inc."},
                }
            )
        )
        create_project = AsyncMock(
            return_value=json.dumps({"success": True, "project": {"id": 99}})
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_find_customer", find_customer
        ), patch.object(graph_module, "call_create_project", create_project):
            result = await graph_module.graph.ainvoke(
                initial_state("Create a project for Acme Corporation Inc.")
            )

        find_customer.assert_awaited_once_with("Acme Corporation Inc")
        create_project.assert_awaited_once()
        self.assertNotIn("__interrupt__", result)

    async def test_missing_customer_still_uses_clarification(self):
        list_call = call("list_projects", {"customer_name": "the customer"}, "projects")
        model = FakeModel([AIMessage(content="", tool_calls=[list_call])])
        find_customer = AsyncMock()

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_find_customer", find_customer
        ):
            paused = await graph_module.graph.ainvoke(
                initial_state("List projects for the customer")
            )

        self.assertIn("__interrupt__", paused)
        find_customer.assert_not_awaited()

    async def test_existing_project_does_not_bypass_ambiguous_resolution(self):
        list_call = call(
            "list_projects",
            {"customer_name": "Acme"},
            "projects",
        )
        model = FakeModel([AIMessage(content="", tool_calls=[list_call])])
        list_projects = AsyncMock(
            return_value=json.dumps(
                {"success": True, "projects": [{"id": 61, "name": "Acme Onboarding"}]}
            )
        )
        state = initial_state("Onboard Acme")
        state["customer_resolution"] = {
            "status": "ambiguous",
            "reference": "Acme",
            "candidates": ["Acme Corporation Inc."],
        }

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_list_projects", list_projects
        ):
            paused = await graph_module.graph.ainvoke(state)

        self.assertIn("__interrupt__", paused)
        self.assertEqual(
            RunStatus.WAITING_FOR_CLARIFICATION.value,
            paused["status"],
        )
        list_projects.assert_not_awaited()

    async def test_ambiguous_resolution_blocks_customer_write(self):
        find = call("find_customer", {"name": "Acme"}, "find")
        create = call(
            "create_project",
            {"customer_name": "Acme", "name": "Acme Onboarding"},
            "project",
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[find]),
                AIMessage(content="", tool_calls=[create]),
            ]
        )
        find_customer = AsyncMock(
            return_value=json.dumps(
                {
                    "found": True,
                    "resolution": {
                        "status": "ambiguous",
                        "candidates": [
                            "Acme Corporation Inc. (US)",
                            "Acme Corporation Ltd. (UK)",
                        ],
                    },
                    "customer": {"name": "Acme"},
                }
            )
        )
        create_project = AsyncMock()
        state = initial_state("Onboard Acme")

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_find_customer", find_customer
        ), patch.object(graph_module, "call_create_project", create_project):
            paused = await graph_module.graph.ainvoke(state)

        self.assertIn("__interrupt__", paused)
        question = paused["__interrupt__"][0].value["question"]
        self.assertIn("Acme Corporation Inc. (US)", question)
        self.assertIn("Acme Corporation Ltd. (UK)", question)
        create_project.assert_not_awaited()
        self.assertEqual(1, model.calls)

    async def test_unresolved_customer_write_interrupts_without_mcp(self):
        create = call(
            "create_project",
            {"customer_name": "Acme", "name": "Acme Onboarding"},
            "project",
        )
        model = FakeModel([AIMessage(content="", tool_calls=[create])])
        create_project = AsyncMock()

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_project", create_project
        ):
            paused = await graph_module.graph.ainvoke(initial_state("Onboard Acme"))

        self.assertIn("__interrupt__", paused)
        self.assertIn("specific customer entity", paused["__interrupt__"][0].value["question"])
        create_project.assert_not_awaited()

    async def test_clarification_resume_resolves_then_allows_write(self):
        find = call("find_customer", {"name": "Acme Corporation Inc."}, "find")
        create = call(
            "create_project",
            {"customer_name": "Acme Corporation Inc.", "name": "Acme Onboarding"},
            "project",
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[create]),
                AIMessage(content="", tool_calls=[find]),
                AIMessage(content="", tool_calls=[create]),
                AIMessage(content="", tool_calls=[create]),
                AIMessage(content="Onboarding project created."),
            ]
        )
        create_project = AsyncMock(
            return_value=json.dumps(
                {
                    "success": True,
                    "project": {
                        "id": 7,
                        "customer_id": 1,
                        "name": "Acme Onboarding",
                        "status": "onboarding",
                    },
                }
            )
        )
        find_customer = AsyncMock(
            return_value=json.dumps(
                {
                    "found": True,
                    "resolution": {
                        "status": "resolved",
                        "reference": "Acme Corporation Inc.",
                    },
                    "customer": {
                        "id": 1,
                        "name": "Acme Corporation Inc.",
                        "legal_name": "Acme Corporation Inc.",
                        "billing_contact": "billing@acmecorp.com",
                        "technical_contact": "tech@acmecorp.com",
                    },
                }
            )
        )
        state = initial_state("Onboard Acme")
        config = {"configurable": {"thread_id": state["run_id"]}}

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_project", create_project
        ), patch.object(graph_module, "call_find_customer", find_customer), patch.object(
            graph_module,
            "call_search_documents",
            return_value=(
                '[{"source": "company/onboarding_sop.md", "content": "Onboarding procedure", "score": 5}, '
                '{"source": "contracts/acme_corp_2026.md", "content": "Customer Legal Entity: Acme Corporation Inc.", "score": 5}]'
            ),
        ):
            paused = await graph_module.graph.ainvoke(state, config=config)
            result = await graph_module.graph.ainvoke(
                Command(resume="Acme Corporation Inc."), config=config
            )

        self.assertIn("__interrupt__", paused)
        create_project.assert_awaited_once()
        find_customer.assert_awaited_once_with("Acme Corporation Inc.")
        self.assertEqual("Onboarding project created.", result["messages"][-1].content)
        self.assertEqual("resolved", result["customer_resolution"]["status"])

    async def test_resolution_from_a_different_run_is_not_reused(self):
        create = call(
            "create_project",
            {"customer_name": "Acme Corporation Inc.", "name": "New Onboarding"},
            "project",
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[create]),
                AIMessage(content="Project created."),
            ]
        )
        state = initial_state("Create a project for Acme Corporation Inc.")
        state["customer_resolution"] = {
            "status": "resolved",
            "reference": "Acme Corporation Inc.",
            "run_id": str(uuid4()),
            "normalized_reference": "acme corporation inc",
            "lookup_result": {"found": True, "customer": {"id": 1}},
        }
        find_customer = AsyncMock(
            return_value=json.dumps(
                {
                    "found": True,
                    "customer": {"id": 1, "legal_name": "Acme Corporation Inc."},
                }
            )
        )
        create_project = AsyncMock(
            return_value=json.dumps({"success": True, "project": {"id": 7}})
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_find_customer", find_customer
        ), patch.object(graph_module, "call_create_project", create_project):
            await graph_module.graph.ainvoke(state)

        find_customer.assert_awaited_once_with("Acme Corporation Inc")
        create_project.assert_awaited_once()

    async def test_resolution_for_one_customer_cannot_authorize_another(self):
        create = call(
            "create_project",
            {"customer_name": "Globex Corporation", "name": "New Onboarding"},
            "project",
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[create]),
                AIMessage(content="Project created."),
            ]
        )
        state = initial_state("Create a project for Globex Corporation.")
        state["customer_resolution"] = {
            "status": "resolved",
            "reference": "Acme Corporation Inc.",
            "run_id": state["run_id"],
            "normalized_reference": "acme corporation inc",
            "lookup_result": {"found": True, "customer": {"id": 1}},
        }
        find_customer = AsyncMock(
            return_value=json.dumps(
                {
                    "found": True,
                    "customer": {"id": 2, "legal_name": "Globex Corporation"},
                }
            )
        )
        create_project = AsyncMock(
            return_value=json.dumps({"success": True, "project": {"id": 8}})
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_find_customer", find_customer
        ), patch.object(graph_module, "call_create_project", create_project):
            await graph_module.graph.ainvoke(state)

        find_customer.assert_awaited_once_with("Globex Corporation")
        create_project.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
