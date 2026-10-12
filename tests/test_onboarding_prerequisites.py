import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

import app.agent.graph as graph_module
from app.agent.state import RunStatus


def call(name: str, args: dict, identifier: str) -> dict:
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


def state() -> dict:
    run_id = str(uuid4())
    return {
        "run_id": run_id,
        "goal": "Onboard Acme Corporation Inc.",
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


def searched(source: str, content: str, identifier: str = "search") -> list:
    return [
        AIMessage(content="", tool_calls=[call("search_documents", {"query": "evidence"}, identifier)]),
        ToolMessage(content=json.dumps([{"source": source, "content": content, "score": 1}]), tool_call_id=identifier),
    ]


class OnboardingPrerequisiteTests(unittest.IsolatedAsyncioTestCase):
    def test_generic_knowledge_is_insufficient(self):
        current = state()
        current["messages"] = [
            *searched("company/onboarding_sop.md", "Onboarding procedure"),
            AIMessage(content="", tool_calls=[call("create_project", {"customer_name": "Acme Corporation Inc.", "name": "Plan"}, "create")]),
        ]
        self.assertTrue(graph_module._requires_onboarding_knowledge(current))
        self.assertIn("governing contract for Acme Corporation Inc.", graph_module._onboarding_knowledge_gaps(current))

    def test_exact_contract_and_sop_evidence_permit_write(self):
        current = state()
        current["messages"] = [
            *searched("company/onboarding_sop.md", "Onboarding procedure", "sop"),
            *searched("contracts/acme_corp_2026.md", "Customer Legal Entity: Acme Corporation Inc.", "contract"),
            AIMessage(content="", tool_calls=[call("create_project", {"customer_name": "Acme Corporation Inc.", "name": "Plan"}, "create")]),
        ]
        self.assertFalse(graph_module._requires_onboarding_knowledge(current))
        self.assertEqual([], graph_module._onboarding_knowledge_gaps(current))

    def test_unrelated_contract_evidence_is_insufficient(self):
        current = state()
        current["messages"] = [
            *searched("company/onboarding_sop.md", "Onboarding procedure", "sop"),
            *searched("contracts/acme_corporation_ltd_2026.md", "Customer Legal Entity: Acme Corporation Ltd.", "contract"),
            AIMessage(content="", tool_calls=[call("create_project", {"customer_name": "Acme Corporation Inc.", "name": "Plan"}, "create")]),
        ]
        self.assertTrue(graph_module._requires_onboarding_knowledge(current))

    async def test_task_creation_is_deferred_until_exact_project_is_inspected(self):
        current = state()
        current["messages"] = [AIMessage(content="", tool_calls=[call("create_task", {"project_id": 42, "title": "Review"}, "task")])]
        with patch.object(graph_module, "call_create_task", AsyncMock()) as create_task:
            result = await graph_module.execute_tool(current)
        create_task.assert_not_awaited()
        payload = graph_module.parse_tool_result(result["last_tool_result"]["tools"][0]["content"])
        self.assertEqual("task_inspection_required", payload["error_type"])

    async def test_other_project_task_inspection_does_not_permit_creation(self):
        current = state()
        current["messages"] = [
            AIMessage(content="", tool_calls=[call("list_tasks", {"project_id": 41}, "other")]),
            ToolMessage(content=json.dumps({"success": True, "tasks": []}), tool_call_id="other"),
            AIMessage(content="", tool_calls=[call("create_task", {"project_id": 42, "title": "Review"}, "task")]),
        ]
        with patch.object(graph_module, "call_create_task", AsyncMock()) as create_task:
            result = await graph_module.execute_tool(current)
        create_task.assert_not_awaited()
        payload = graph_module.parse_tool_result(result["last_tool_result"]["tools"][0]["content"])
        self.assertEqual("task_inspection_required", payload["error_type"])

    async def test_exact_project_task_inspection_permits_creation(self):
        current = state()
        current["messages"] = [
            AIMessage(content="", tool_calls=[call("list_tasks", {"project_id": 42}, "listed")]),
            ToolMessage(content=json.dumps({"success": True, "tasks": []}), tool_call_id="listed"),
            AIMessage(content="", tool_calls=[call("create_task", {"project_id": 42, "title": "Review"}, "task")]),
        ]
        created = {"success": True, "task": {"id": 43, "project_id": 42, "title": "Review", "status": "open"}}
        with patch.object(graph_module, "call_create_task", AsyncMock(return_value=json.dumps(created))) as create_task:
            result = await graph_module.execute_tool(current)
        create_task.assert_awaited_once_with(42, "Review")
        self.assertTrue(result["last_tool_succeeded"])

    def test_only_verified_writes_support_creation_claims(self):
        self.assertEqual(
            {"projects": [], "tasks": []},
            graph_module._verified_onboarding_claims({"onboarding_verification_result": None}),
        )
        self.assertEqual(
            {"projects": [], "tasks": []},
            graph_module._verified_onboarding_claims({"onboarding_verification_result": {"status": "FAIL"}}),
        )
        self.assertEqual(
            {"projects": [9], "tasks": [10]},
            graph_module._verified_onboarding_claims({"onboarding_verification_result": {"status": "PASS", "writes": [{"kind": "project", "project_id": 9}, {"kind": "task", "task_id": 10}]}}),
        )


if __name__ == "__main__":
    unittest.main()
