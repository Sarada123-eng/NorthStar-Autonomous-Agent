import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

import app.agent.graph as graph_module
import app.retrieval.lexical as lexical
from app.agent.state import RunStatus

LEGAL = "Acme Corporation Inc."


def call(name: str, args: dict, identifier: str) -> dict:
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


def base_state(business_checks: bool = True, goal: str = f"Onboard {LEGAL}") -> dict:
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
            "reference": LEGAL,
            "run_id": run_id,
            "normalized_reference": "acme corporation inc",
            "customer": {"id": 1, "legal_name": LEGAL},
        },
        "verification_result": None,
        "onboarding_verification_result": None,
        "invoice_verification_result": None,
        "verification_repair_count": 0,
        "business_checks": business_checks,
        "completion_check_count": 0,
        "onboarding_completion_result": None,
        "tool_step_count": 0,
        "retry_counts": {},
        "last_action_signature": None,
        "consecutive_action_count": 0,
        "last_failed_non_retryable_action": None,
        "execution_stop_reason": None,
        "error": None,
    }


def observed_project_messages() -> list:
    return [
        AIMessage(content="", tool_calls=[call("list_projects", {"customer_name": LEGAL}, "lp")]),
        ToolMessage(
            content=json.dumps({"success": True, "projects": [{"id": 20, "customer_id": 1, "name": "Onboarding", "status": "onboarding"}]}),
            tool_call_id="lp",
        ),
        AIMessage(content="", tool_calls=[call("list_tasks", {"project_id": 20}, "lt")]),
        ToolMessage(content=json.dumps({"success": True, "tasks": []}), tool_call_id="lt"),
    ]


def contract_search_messages() -> list:
    return [
        AIMessage(content="", tool_calls=[call("search_documents", {"query": "x"}, "s")]),
        ToolMessage(
            content=json.dumps(
                [
                    {"source": "contracts/acme_corp_2026.md", "content": f"Customer: {LEGAL}", "score": 3},
                    {"source": "company/onboarding_sop.md", "content": "Onboarding procedure", "score": 2},
                ]
            ),
            tool_call_id="s",
        ),
    ]


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        return next(self.responses)


def review(*items) -> AIMessage:
    return AIMessage(content=json.dumps({"required_items": list(items)}))


def read_document_payload(source: str) -> str:
    return json.dumps({"success": True, "source": source, "content": f"{LEGAL} document text for {source}"})


TASKS = [{"id": 30, "project_id": 20, "title": "Security review", "status": "open"}]
ACME_REQUIREMENTS = [
    "Mandatory Security Review",
    "Environment Encryption & DPA Verification",
    "Infrastructure Capacity Review",
    "Technical Architecture Assessment",
    "Timeline Risk & Resource Escalation",
    "Custom Log Streaming Configuration",
]


def patched_reads(tasks):
    return (
        patch.object(graph_module, "call_read_document", AsyncMock(side_effect=read_document_payload)),
        patch.object(graph_module, "call_list_projects", AsyncMock(return_value=json.dumps({"success": True, "projects": [{"id": 20, "customer_id": 1, "name": "Onboarding"}]}))),
        patch.object(graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": tasks}))),
    )


class CompletionRoutingTests(unittest.TestCase):
    def final_state(self, **overrides) -> dict:
        current = base_state()
        current["messages"] = [AIMessage(content="Done.")]
        current.update(overrides)
        return current

    def test_check_is_off_unless_business_checks_enabled(self):
        self.assertEqual(END, graph_module.route_after_decision(self.final_state(business_checks=False)))

    def test_check_runs_for_resolved_onboarding_goal(self):
        self.assertEqual("verify_requirements", graph_module.route_after_decision(self.final_state()))

    def test_check_skips_non_onboarding_goals_and_unresolved_customers(self):
        self.assertEqual(END, graph_module.route_after_decision(self.final_state(goal="Find the latest invoice for Acme")))
        unresolved = self.final_state()
        unresolved["customer_resolution"] = None
        self.assertEqual(END, graph_module.route_after_decision(unresolved))

    def test_check_stops_after_repair_budget(self):
        self.assertEqual(END, graph_module.route_after_decision(self.final_state(completion_check_count=3)))


class CompletionReviewTests(unittest.IsolatedAsyncioTestCase):
    async def run_check(self, model_response, tasks=TASKS, count=0):
        current = base_state()
        current["messages"] = [*contract_search_messages(), *observed_project_messages()]
        current["completion_check_count"] = count
        reads = patched_reads(tasks)
        with patch.object(graph_module, "get_llm", return_value=FakeModel([model_response])), reads[0], reads[1], reads[2]:
            return await graph_module.verify_requirements(current)

    async def test_missing_required_item_triggers_repair_message(self):
        update = await self.run_check(
            review(
                {"item": "Security review", "basis": "SOP 5.1", "satisfied_by_task_id": 30},
                {"item": "Capacity review", "basis": "SOP 5.2", "satisfied_by_task_id": None},
            )
        )
        result = update["onboarding_completion_result"]
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(["Capacity review"], result["missing"])
        self.assertEqual(1, update["completion_check_count"])
        self.assertIsInstance(update["messages"][0], HumanMessage)
        self.assertIn("Capacity review", update["messages"][0].content)
        self.assertNotIn("execution_stop_reason", update)
        self.assertEqual(
            [
                "contracts/acme_corp_2026.md",
                "company/onboarding_sop.md",
                "company/security_policy.md",
            ],
            [item["source"] for item in update["verification_evidence"]],
        )
        self.assertIn(LEGAL, update["verification_evidence"][0]["excerpt"])

    async def test_generic_task_cannot_satisfy_unrelated_requirements(self):
        generic_task = {
            "id": 30,
            "project_id": 20,
            "title": "Initial Setup for Acme Corporation Inc.",
            "status": "open",
        }
        update = await self.run_check(
            review(
                *[
                    {"item": item, "basis": "contract", "satisfied_by_task_id": 30}
                    for item in ACME_REQUIREMENTS
                ]
            ),
            tasks=[generic_task],
        )
        result = update["onboarding_completion_result"]
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(ACME_REQUIREMENTS, result["missing"])
        self.assertIn("no matching task exists", update["messages"][0].content)
        self.assertTrue(
            all(
                not item["coverage_evidence"]["covered"]
                for item in result["required_items"]
            )
        )

    async def test_partial_title_coverage_leaves_other_requirements_missing(self):
        tasks = [
            {"id": 30, "project_id": 20, "title": "Security review", "status": "open"},
            {"id": 31, "project_id": 20, "title": "Infrastructure capacity review", "status": "open"},
        ]
        claims = [
            {"item": ACME_REQUIREMENTS[0], "basis": "contract", "satisfied_by_task_id": 30},
            {"item": ACME_REQUIREMENTS[1], "basis": "contract", "satisfied_by_task_id": 30},
            {"item": ACME_REQUIREMENTS[2], "basis": "contract", "satisfied_by_task_id": 31},
            {"item": ACME_REQUIREMENTS[3], "basis": "contract", "satisfied_by_task_id": 31},
            {"item": ACME_REQUIREMENTS[4], "basis": "contract", "satisfied_by_task_id": 31},
            {"item": ACME_REQUIREMENTS[5], "basis": "contract", "satisfied_by_task_id": 31},
        ]
        update = await self.run_check(review(*claims), tasks=tasks)
        result = update["onboarding_completion_result"]
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(
            [
                "Environment Encryption & DPA Verification",
                "Technical Architecture Assessment",
                "Timeline Risk & Resource Escalation",
                "Custom Log Streaming Configuration",
            ],
            result["missing"],
        )

    async def test_explicit_task_titles_can_cover_all_requirements(self):
        tasks = [
            {"id": index, "project_id": 20, "title": item, "status": "open"}
            for index, item in enumerate(ACME_REQUIREMENTS, start=30)
        ]
        update = await self.run_check(
            review(
                *[
                    {"item": item, "basis": "contract", "satisfied_by_task_id": index}
                    for index, item in enumerate(ACME_REQUIREMENTS, start=30)
                ]
            ),
            tasks=tasks,
        )
        result = update["onboarding_completion_result"]
        self.assertEqual("PASS", result["status"])
        self.assertEqual([], result["missing"])

    async def test_one_explicit_title_can_cover_multiple_requirements(self):
        tasks = [
            {
                "id": 30,
                "project_id": 20,
                "title": "Security review and environment encryption DPA verification",
                "status": "open",
            }
        ]
        update = await self.run_check(
            review(
                {"item": ACME_REQUIREMENTS[0], "basis": "contract", "satisfied_by_task_id": 30},
                {"item": ACME_REQUIREMENTS[1], "basis": "contract", "satisfied_by_task_id": 30},
            ),
            tasks=tasks,
        )
        self.assertEqual("PASS", update["onboarding_completion_result"]["status"])
        self.assertEqual(
            [30, 30],
            [item["satisfied_by_task_id"] for item in update["onboarding_completion_result"]["required_items"]],
        )

    async def test_all_items_covered_passes(self):
        update = await self.run_check(
            review({"item": "Security review", "basis": "SOP 5.1", "satisfied_by_task_id": 30})
        )
        self.assertEqual("PASS", update["onboarding_completion_result"]["status"])
        self.assertNotIn("messages", update)

    async def test_claim_of_nonexistent_task_does_not_count(self):
        update = await self.run_check(
            review({"item": "Security review", "basis": "SOP 5.1", "satisfied_by_task_id": 999})
        )
        self.assertEqual("FAIL", update["onboarding_completion_result"]["status"])
        self.assertEqual(["Security review"], update["onboarding_completion_result"]["missing"])

    async def test_exhausted_repairs_stop_and_report(self):
        update = await self.run_check(
            review({"item": "Capacity review", "basis": "SOP 5.2", "satisfied_by_task_id": None}),
            count=2,
        )
        self.assertEqual("FAIL", update["onboarding_completion_result"]["status"])
        self.assertEqual("onboarding_requirements_incomplete", update["execution_stop_reason"])
        self.assertIn("Do not claim onboarding is complete", update["messages"][0].content)

    async def test_generic_task_stops_honestly_after_existing_repair_limit(self):
        generic_task = {
            "id": 30,
            "project_id": 20,
            "title": "Initial Setup for Acme Corporation Inc.",
            "status": "open",
        }
        update = await self.run_check(
            review(
                *[
                    {"item": item, "basis": "contract", "satisfied_by_task_id": 30}
                    for item in ACME_REQUIREMENTS
                ]
            ),
            tasks=[generic_task],
            count=2,
        )
        self.assertEqual("FAIL", update["onboarding_completion_result"]["status"])
        self.assertEqual("onboarding_requirements_incomplete", update["execution_stop_reason"])
        self.assertIn("Do not claim onboarding is complete", update["messages"][0].content)

    async def test_unparseable_reviewer_output_fails_open_and_is_recorded(self):
        update = await self.run_check(AIMessage(content="not json"))
        self.assertEqual("SKIPPED", update["onboarding_completion_result"]["status"])
        self.assertNotIn("messages", update)

    async def test_missing_contract_fails_open_and_is_recorded(self):
        current = base_state()
        with patch.object(graph_module, "call_search_documents", AsyncMock(return_value="[]")):
            update = await graph_module.verify_requirements(current)
        self.assertEqual("SKIPPED", update["onboarding_completion_result"]["status"])

    def test_routing_after_check(self):
        self.assertEqual(END, graph_module.route_after_completion_check({"onboarding_completion_result": {"status": "PASS"}}))
        self.assertEqual("decide", graph_module.route_after_completion_check({"onboarding_completion_result": {"status": "FAIL"}, "execution_stop_reason": None}))
        self.assertEqual("answer", graph_module.route_after_completion_check({"onboarding_completion_result": {"status": "FAIL"}, "execution_stop_reason": "onboarding_requirements_incomplete"}))


class CompletionLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_item_is_repaired_then_passes(self):
        created = {"id": 31, "project_id": 20, "title": "Capacity review", "status": "open"}
        existing = TASKS[0]
        state = {"created": False}

        async def list_tasks(project_id):
            tasks = [existing, created] if state["created"] else [existing]
            return json.dumps({"success": True, "tasks": tasks})

        async def create_task(project_id, title):
            state["created"] = True
            return json.dumps({"success": True, "task": created})

        model = FakeModel(
            [
                AIMessage(content="Onboarding complete."),
                review(
                    {"item": "Security review", "basis": "SOP 5.1", "satisfied_by_task_id": 30},
                    {"item": "Capacity review", "basis": "SOP 5.2", "satisfied_by_task_id": None},
                ),
                AIMessage(content="", tool_calls=[call("create_task", {"project_id": 20, "title": "Capacity review"}, "ct")]),
                AIMessage(content="Capacity review task created."),
                review(
                    {"item": "Security review", "basis": "SOP 5.1", "satisfied_by_task_id": 30},
                    {"item": "Capacity review", "basis": "SOP 5.2", "satisfied_by_task_id": 31},
                ),
            ]
        )
        initial = base_state()
        initial["messages"] = [*contract_search_messages(), *observed_project_messages()]
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_read_document", AsyncMock(side_effect=read_document_payload)
        ), patch.object(
            graph_module,
            "call_list_projects",
            AsyncMock(return_value=json.dumps({"success": True, "projects": [{"id": 20, "customer_id": 1, "name": "Onboarding", "status": "onboarding"}]})),
        ), patch.object(graph_module, "call_list_tasks", AsyncMock(side_effect=list_tasks)), patch.object(
            graph_module, "call_create_task", AsyncMock(side_effect=create_task)
        ) as create:
            result = await graph_module.graph.ainvoke(
                initial, {"configurable": {"thread_id": str(uuid4())}}
            )

        create.assert_awaited_once_with(20, "Capacity review")
        self.assertEqual("PASS", result["onboarding_completion_result"]["status"])
        self.assertEqual(2, result["completion_check_count"])
        self.assertEqual("Capacity review task created.", result["messages"][-1].content)

    async def test_completion_failure_reuses_verified_task_observation_for_repair(self):
        """A completion FAIL must repair, not repeat the immediately prior task read."""
        generic = {
            "id": 30,
            "project_id": 20,
            "title": "Initial Setup for Acme Corporation Inc.",
            "status": "open",
        }
        repair = {
            "id": 31,
            "project_id": 20,
            "title": "Capacity review",
            "status": "open",
        }
        state = {"created": False}

        async def list_tasks(project_id):
            self.assertEqual(20, project_id)
            return json.dumps(
                {"success": True, "tasks": [generic, repair] if state["created"] else [generic]}
            )

        async def create_task(project_id, title):
            self.assertEqual((20, "Capacity review"), (project_id, title))
            state["created"] = True
            return json.dumps({"success": True, "task": repair})

        model = FakeModel(
            [
                AIMessage(content="Onboarding complete."),
                review(
                    {"item": "Capacity review", "basis": "SOP 5.2", "satisfied_by_task_id": 30},
                ),
                # The repair planner must create the known missing task rather
                # than issue another list_tasks call for the same project.
                AIMessage(
                    content="",
                    tool_calls=[call("create_task", {"project_id": 20, "title": "Capacity review"}, "repair")],
                ),
                AIMessage(content="Capacity review task created."),
                review(
                    {"item": "Capacity review", "basis": "SOP 5.2", "satisfied_by_task_id": 31},
                ),
            ]
        )
        initial = base_state()
        initial["messages"] = [*contract_search_messages(), *observed_project_messages()]
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_read_document", AsyncMock(side_effect=read_document_payload)
        ), patch.object(
            graph_module,
            "call_list_projects",
            AsyncMock(return_value=json.dumps({"success": True, "projects": [{"id": 20, "customer_id": 1, "name": "Onboarding", "status": "onboarding"}]})),
        ), patch.object(graph_module, "call_list_tasks", AsyncMock(side_effect=list_tasks)), patch.object(
            graph_module, "call_create_task", AsyncMock(side_effect=create_task)
        ) as create:
            result = await graph_module.graph.ainvoke(
                initial, {"configurable": {"thread_id": str(uuid4())}}
            )

        create.assert_awaited_once_with(20, "Capacity review")
        self.assertTrue(state["created"])
        self.assertEqual("PASS", result["onboarding_verification_result"]["status"])
        self.assertEqual("task", result["onboarding_verification_result"]["writes"][-1]["kind"])
        self.assertEqual("PASS", result["onboarding_completion_result"]["status"])
        self.assertEqual(2, result["completion_check_count"])
        self.assertIsNone(result["execution_stop_reason"])


class ProjectScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unobserved_project_is_blocked_before_write_and_read(self):
        for name, args in (("create_task", {"project_id": 2, "title": "X"}), ("list_tasks", {"project_id": 2})):
            with self.subTest(tool=name):
                current = base_state()
                current["messages"] = [
                    *observed_project_messages(),
                    AIMessage(content="", tool_calls=[call(name, args, "t")]),
                ]
                with patch.object(graph_module, "call_create_task", AsyncMock()) as create, patch.object(
                    graph_module, "call_list_tasks", AsyncMock()
                ) as listed:
                    result = await graph_module.execute_tool(current)
                create.assert_not_awaited()
                listed.assert_not_awaited()
                payload = graph_module.parse_tool_result(result["last_tool_result"]["tools"][0]["content"])
                # create_task is also blocked earlier by the existing inspection rule.
                self.assertIn(payload["error_type"], {"project_scope_violation", "task_inspection_required"})

    async def test_inspected_but_foreign_project_is_blocked_before_write(self):
        current = base_state()
        current["messages"] = [
            *observed_project_messages(),
            AIMessage(content="", tool_calls=[call("list_tasks", {"project_id": 2}, "other")]),
            ToolMessage(content=json.dumps({"success": True, "tasks": []}), tool_call_id="other"),
            AIMessage(content="", tool_calls=[call("create_task", {"project_id": 2, "title": "X"}, "t")]),
        ]
        with patch.object(graph_module, "call_create_task", AsyncMock()) as create:
            result = await graph_module.execute_tool(current)
        create.assert_not_awaited()
        payload = graph_module.parse_tool_result(result["last_tool_result"]["tools"][0]["content"])
        self.assertEqual("project_scope_violation", payload["error_type"])

    async def test_observed_project_is_allowed(self):
        current = base_state()
        current["messages"] = [
            *observed_project_messages(),
            AIMessage(content="", tool_calls=[call("list_tasks", {"project_id": 20}, "t")]),
        ]
        with patch.object(graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": []}))) as listed:
            await graph_module.execute_tool(current)
        listed.assert_awaited_once_with(20)

    async def test_guard_is_inactive_without_business_checks(self):
        current = base_state(business_checks=False)
        current["messages"] = [AIMessage(content="", tool_calls=[call("list_tasks", {"project_id": 2}, "t")])]
        with patch.object(graph_module, "call_list_tasks", AsyncMock(return_value=json.dumps({"success": True, "tasks": []}))) as listed:
            await graph_module.execute_tool(current)
        listed.assert_awaited_once_with(2)


class CanonicalCustomerArgsTests(unittest.TestCase):
    def resolved_for(self, reference: str) -> dict:
        current = base_state()
        current["customer_resolution"]["reference"] = reference
        current["customer_resolution"]["normalized_reference"] = graph_module._normalize_customer_reference(reference)
        return current

    def test_abbreviated_name_is_replaced_by_the_resolved_legal_name(self):
        current = self.resolved_for("Acme Corp")
        for tool in ("list_projects", "create_project", "list_invoices", "log_invoice"):
            adjusted = graph_module._canonical_customer_args(
                current, call(tool, {"customer_name": "Acme Corp"}, "x")
            )
            self.assertEqual(LEGAL, adjusted["args"]["customer_name"], tool)

    def test_other_names_tools_and_disabled_checks_are_left_alone(self):
        current = self.resolved_for("Acme Corp")
        other = call("list_projects", {"customer_name": "Globex"}, "x")
        self.assertEqual(other, graph_module._canonical_customer_args(current, other))
        unrelated = call("find_customer", {"name": "Acme Corp"}, "x")
        self.assertEqual(unrelated, graph_module._canonical_customer_args(current, unrelated))
        disabled = self.resolved_for("Acme Corp")
        disabled["business_checks"] = False
        same = call("list_projects", {"customer_name": "Acme Corp"}, "x")
        self.assertEqual(same, graph_module._canonical_customer_args(disabled, same))


class CompletionScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_tasks_of_projects_this_run_did_not_use_do_not_count(self):
        async def list_tasks(project_id):
            tasks = {
                20: TASKS,
                21: [{"id": 99, "project_id": 21, "title": "Capacity review", "status": "open"}],
            }[project_id]
            return json.dumps({"success": True, "tasks": tasks})

        projects = json.dumps(
            {
                "success": True,
                "projects": [
                    {"id": 20, "customer_id": 1, "name": "Onboarding"},
                    {"id": 21, "customer_id": 1, "name": "Earlier run"},
                ],
            }
        )
        current = base_state()
        current["messages"] = [*contract_search_messages(), *observed_project_messages()]
        model = FakeModel(
            [
                review(
                    {"item": "Security review", "basis": "SOP 5.1", "satisfied_by_task_id": 30},
                    {"item": "Capacity review", "basis": "SOP 5.2", "satisfied_by_task_id": 99},
                )
            ]
        )
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_read_document", AsyncMock(side_effect=read_document_payload)
        ), patch.object(graph_module, "call_list_projects", AsyncMock(return_value=projects)), patch.object(
            graph_module, "call_list_tasks", AsyncMock(side_effect=list_tasks)
        ):
            update = await graph_module.verify_requirements(current)
        self.assertEqual(["Capacity review"], update["onboarding_completion_result"]["missing"])

    async def test_no_project_touched_by_this_run_means_nothing_is_covered(self):
        current = base_state()
        current["messages"] = contract_search_messages()
        model = FakeModel(
            [review({"item": "Security review", "basis": "SOP 5.1", "satisfied_by_task_id": 30})]
        )
        reads = patched_reads(TASKS)
        with patch.object(graph_module, "get_llm", return_value=model), reads[0], reads[1], reads[2] as listed:
            update = await graph_module.verify_requirements(current)
        listed.assert_not_awaited()
        self.assertEqual("FAIL", update["onboarding_completion_result"]["status"])


class ParallelToolCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_tool_call_gets_a_result_when_a_guard_stops_the_batch(self):
        current = base_state()
        current["messages"] = [
            *observed_project_messages(),
            AIMessage(
                content="",
                tool_calls=[
                    call("list_tasks", {"project_id": 2}, "blocked"),
                    call("list_tasks", {"project_id": 20}, "skipped"),
                ],
            ),
        ]
        with patch.object(graph_module, "call_list_tasks", AsyncMock()) as listed:
            result = await graph_module.execute_tool(current)
        listed.assert_not_awaited()
        answered = {message.tool_call_id: message for message in result["messages"]}
        self.assertEqual({"blocked", "skipped"}, set(answered))
        self.assertEqual(
            "not_executed",
            graph_module.parse_tool_result(answered["skipped"].content)["error_type"],
        )


class ReadDocumentTests(unittest.TestCase):
    def test_reads_markdown_inside_root_and_rejects_everything_else(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "company").mkdir()
            (root / "company" / "sop.md").write_text("Full text", encoding="utf-8")
            (root.parent / "secret.md").write_text("secret", encoding="utf-8")
            (root / "company" / "notes.txt").write_text("x", encoding="utf-8")
            with patch.object(lexical, "KNOWLEDGE_ROOT", root):
                ok = lexical.read_document("company/sop.md")
                self.assertTrue(ok["success"])
                self.assertEqual("Full text", ok["content"])
                for bad in ("../secret.md", "company/notes.txt", "company/missing.md", "company"):
                    self.assertFalse(lexical.read_document(bad)["success"], bad)


if __name__ == "__main__":
    unittest.main()
