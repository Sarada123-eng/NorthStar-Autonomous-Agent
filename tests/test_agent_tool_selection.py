import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage

import app.agent.graph as graph_module
from app.agent.state import RunStatus


def initial_state(goal: str) -> dict:
    return {
        # Every invocation is a distinct checkpointed run. Reusing a constant
        # thread ID would deliberately reuse LangGraph's saved state.
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
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        return next(self.responses)


class AgentToolSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_onboarding_write_requires_documented_procedure_lookup(self):
        write_call = {
            "name": "create_project",
            "args": {"customer_name": "Globex", "name": "Onboarding Globex"},
            "id": "project-call",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[write_call]),
                AIMessage(content="I consulted the onboarding procedure before planning."),
            ]
        )

        state = initial_state("Onboard Globex.")
        state["customer_resolution"] = {
            "status": "resolved",
            "reference": "Globex",
            "run_id": state["run_id"],
            "normalized_reference": "globex",
            "lookup_result": {"found": True, "customer": {"id": 2, "name": "Globex"}},
        }

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_search_documents",
            return_value='[{"source": "company/onboarding_sop.md", "score": 5}]',
        ) as knowledge_search, patch.object(
            graph_module, "call_create_project"
        ) as create_project:
            result = await graph_module.graph.ainvoke(state)

        knowledge_search.assert_awaited_once_with(
            "Onboard Globex. onboarding procedure policies"
        )
        create_project.assert_not_awaited()
        self.assertEqual(
            "I consulted the onboarding procedure before planning.",
            result["messages"][-1].content,
        )

    async def test_internal_question_uses_mcp_tool(self):
        tool_call = {
            "name": "search_documents",
            "args": {"query": "What security requirements apply to Acme?"},
            "id": "call-1",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="Acme must follow the documented security requirements."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_search_documents",
            return_value='[{"source": "company/security_policy.md", "score": 4}]',
        ) as mcp_search:
            result = await graph_module.graph.ainvoke(
                initial_state("What security requirements apply to Acme?")
            )

        mcp_search.assert_awaited_once()
        self.assertTrue(result["last_tool_succeeded"])
        self.assertEqual(
            "Acme must follow the documented security requirements.",
            result["messages"][-1].content,
        )

    async def test_other_internal_questions_use_mcp_tool(self):
        for index, goal in enumerate(
            [
                "What does the onboarding process require for Acme?",
                "Find the latest invoice issued to Acme.",
            ]
        ):
            with self.subTest(goal=goal):
                tool_call = {
                    "name": "search_documents",
                    "args": {"query": goal},
                    "id": f"call-{index}",
                    "type": "tool_call",
                }
                model = FakeModel(
                    [
                        AIMessage(content="", tool_calls=[tool_call]),
                        AIMessage(content="Here is the relevant internal information."),
                    ]
                )
                with patch.object(
                    graph_module, "get_llm", return_value=model
                ), patch.object(
                    graph_module,
                    "call_search_documents",
                    return_value="retrieved internal result",
                ) as mcp_search:
                    result = await graph_module.graph.ainvoke(initial_state(goal))

                mcp_search.assert_awaited_once_with(goal)
                self.assertEqual(
                    "Here is the relevant internal information.",
                    result["messages"][-1].content,
                )

    async def test_simple_question_skips_mcp_tool(self):
        model = FakeModel([AIMessage(content="2 + 2 is 4.")])

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_search_documents"
        ) as mcp_search:
            result = await graph_module.graph.ainvoke(initial_state("What is 2 + 2?"))

        mcp_search.assert_not_awaited()
        self.assertEqual("2 + 2 is 4.", result["messages"][-1].content)

    async def test_customer_question_uses_operations_mcp_tool(self):
        tool_call = {
            "name": "find_customer",
            "args": {"name": "Acme"},
            "id": "customer-call",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="Acme exists and is onboarding."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_find_customer",
            return_value='{"found": true, "customer": {"name": "Acme"}}',
        ) as operations_search:
            result = await graph_module.graph.ainvoke(
                initial_state("Does Acme already exist as a customer?")
            )

        operations_search.assert_awaited_once_with("Acme")
        self.assertEqual("Acme exists and is onboarding.", result["messages"][-1].content)

    async def test_create_request_uses_operations_create_tool(self):
        tool_call = {
            "name": "create_customer",
            "args": {
                "name": "NewCo",
                "billing_contact": "billing@newco.example",
                "technical_contact": "tech@newco.example",
            },
            "id": "create-call",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="NewCo was created successfully."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_create_customer",
            return_value='{"success": true, "customer": {"name": "NewCo"}}',
        ) as create_call:
            result = await graph_module.graph.ainvoke(
                initial_state(
                    "Create a new customer called NewCo with billing contact "
                    "billing@newco.example and technical contact tech@newco.example."
                )
            )

        create_call.assert_awaited_once_with(
            "NewCo",
            "billing@newco.example",
            "tech@newco.example",
        )
        self.assertTrue(result["last_tool_succeeded"])
        self.assertEqual("NewCo was created successfully.", result["messages"][-1].content)

    async def test_onboarding_cannot_create_customer_to_request_contacts(self):
        tool_call = {
            "name": "create_customer",
            "args": {
                "name": "Acme Corporation Inc.",
                "billing_contact": "",
                "technical_contact": "",
            },
            "id": "invalid-create-call",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="I will look up the existing customer."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_customer"
        ) as create_call:
            result = await graph_module.graph.ainvoke(
                initial_state("Onboard Acme Corporation Inc.")
            )

        create_call.assert_not_awaited()
        self.assertEqual(
            "I will look up the existing customer.", result["messages"][-1].content
        )
        self.assertEqual(
            "customer_creation_not_requested",
            json.loads(
                result["last_tool_result"]["tools"][0]["content"]
            )["error_type"],
        )

    async def test_duplicate_create_failure_is_replanned_with_customer_lookup(self):
        tool_call = {
            "name": "create_customer",
            "args": {
                "name": "Globex",
                "billing_contact": "billing@globex.example",
                "technical_contact": "tech@globex.example",
            },
            "id": "duplicate-call",
            "type": "tool_call",
        }
        recovery_call = {
            "name": "find_customer",
            "args": {"name": "Globex"},
            "id": "recovery-call",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="", tool_calls=[recovery_call]),
                AIMessage(content="Globex already exists and is onboarding."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_create_customer",
            return_value=(
                '{"success": false, "error_type": "duplicate", '
                '"message": "Customer \'Globex\' already exists."}'
            ),
        ) as create_call, patch.object(
            graph_module,
            "call_find_customer",
            return_value='{"found": true, "customer": {"name": "Globex"}}',
        ) as find_call:
            result = await graph_module.graph.ainvoke(
                initial_state("Create a customer named Globex.")
            )

        create_call.assert_awaited_once()
        find_call.assert_awaited_once_with("Globex")
        self.assertTrue(result["last_tool_succeeded"])
        self.assertEqual(
            "Globex already exists and is onboarding.",
            result["messages"][-1].content,
        )

    async def test_successful_tools_return_to_decision_loop(self):
        first_call = {
            "name": "search_documents",
            "args": {"query": "Acme contract identity"},
            "id": "search-call",
            "type": "tool_call",
        }
        second_call = {
            "name": "find_customer",
            "args": {"name": "Acme"},
            "id": "find-call",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[first_call]),
                AIMessage(content="", tool_calls=[second_call]),
                AIMessage(content="The customer record and contract were reviewed."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_search_documents",
            return_value="contract identity result",
        ) as search_call, patch.object(
            graph_module,
            "call_find_customer",
            return_value='{"found": true, "customer": {"name": "Acme"}}',
        ) as find_call:
            result = await graph_module.graph.ainvoke(
                initial_state("Review Acme identity and current customer record.")
            )

        search_call.assert_awaited_once_with("Acme contract identity")
        find_call.assert_awaited_once_with("Acme")
        self.assertEqual(
            "The customer record and contract were reviewed.",
            result["messages"][-1].content,
        )

    async def test_repeated_failed_action_is_blocked(self):
        duplicate = (
            '{"success": false, "error_type": "duplicate", '
            '"message": "Customer \'Acme\' already exists."}'
        )
        create_call = {
            "name": "create_customer",
            "args": {
                "name": "Acme",
                "billing_contact": "billing@acme.example",
                "technical_contact": "tech@acme.example",
            },
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[{**create_call, "id": "call-1"}]),
                AIMessage(content="", tool_calls=[{**create_call, "id": "call-2"}]),
                AIMessage(content="The duplicate action was blocked."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_customer", return_value=duplicate
        ) as create_customer_call:
            result = await graph_module.graph.ainvoke(
                initial_state("Create a customer named Acme.")
            )

        create_customer_call.assert_awaited_once()
        self.assertEqual("repeated_action", result["execution_stop_reason"])
        self.assertEqual(
            "The duplicate action was blocked.",
            result["messages"][-1].content,
        )

    async def test_execution_budget_produces_explicit_bounded_stop(self):
        calls = [
            {
                "name": "search_documents",
                "args": {"query": f"query-{index}"},
                "id": f"call-{index}",
                "type": "tool_call",
            }
            for index in range(3)
        ]
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[calls[0]]),
                AIMessage(content="", tool_calls=[calls[1]]),
                AIMessage(content="", tool_calls=[calls[2]]),
                AIMessage(content="Execution stopped before completion."),
            ]
        )

        with patch.object(graph_module, "MAX_TOOL_STEPS", 3), patch.object(
            graph_module, "get_llm", return_value=model
        ), patch.object(
            graph_module, "call_search_documents", return_value="result"
        ) as search_call:
            result = await graph_module.graph.ainvoke(
                initial_state("Perform a bounded multi-step investigation.")
            )

        self.assertEqual(3, search_call.await_count)
        self.assertEqual("tool_step_limit", result["execution_stop_reason"])
        self.assertEqual(
            "Execution stopped before completion.",
            result["messages"][-1].content,
        )

    async def test_acme_duplicate_requires_identity_resolution(self):
        create_call = {
            "name": "create_customer",
            "args": {
                "name": "Acme",
                "billing_contact": "billing@acme.example",
                "technical_contact": "tech@acme.example",
            },
            "id": "create-call",
            "type": "tool_call",
        }
        find_call = {
            "name": "find_customer",
            "args": {"name": "Acme"},
            "id": "find-call",
            "type": "tool_call",
        }
        identity_call = {
            "name": "search_documents",
            "args": {"query": "Acme legal name registration tax ID domain"},
            "id": "identity-call",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[create_call]),
                AIMessage(content="", tool_calls=[find_call]),
                AIMessage(content="", tool_calls=[identity_call]),
                AIMessage(
                    content=(
                        "Acme identity is ambiguous because the documents "
                        "describe separate legal entities. Please clarify."
                    )
                ),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_create_customer",
            return_value=(
                '{"success": false, "error_type": "duplicate", '
                '"message": "Customer \'Acme\' already exists."}'
            ),
        ) as create_customer_call, patch.object(
            graph_module,
            "call_find_customer",
            return_value=(
                '{"found": true, "customer": {"name": "Acme", '
                '"billing_contact": "Daniel Okafor"}}'
            ),
        ) as find_customer_call, patch.object(
            graph_module,
            "call_search_documents",
            return_value=(
                "Acme Corporation Inc. and Acme Corporation Ltd. are "
                "separate legal entities with different domains."
            ),
        ) as identity_search_call:
            result = await graph_module.graph.ainvoke(
                initial_state(
                    "Create a customer named Acme with billing contact "
                    "billing@acme.example and technical contact tech@acme.example."
                )
            )

        create_customer_call.assert_awaited_once()
        find_customer_call.assert_awaited_once_with("Acme")
        identity_search_call.assert_awaited_once()
        self.assertIn("ambiguous", result["messages"][-1].content)

    async def test_missing_contacts_does_not_call_create(self):
        model = FakeModel(
            [AIMessage(content="Please provide billing and technical contacts.")]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_create_customer"
        ) as create_call:
            result = await graph_module.graph.ainvoke(
                initial_state("Create a customer named NewCo.")
            )

        create_call.assert_not_awaited()
        self.assertEqual(
            "Please provide billing and technical contacts.",
            result["messages"][-1].content,
        )

    async def test_combined_question_can_call_both_tools(self):
        tool_calls = [
            {
                "name": "search_documents",
                "args": {"query": "What security requirements apply to Acme?"},
                "id": "knowledge-call",
                "type": "tool_call",
            },
            {
                "name": "find_customer",
                "args": {"name": "Acme"},
                "id": "customer-call",
                "type": "tool_call",
            },
        ]
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=tool_calls),
                AIMessage(content="Acme exists and has documented security requirements."),
            ]
        )

        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module, "call_search_documents", return_value="security result"
        ) as knowledge_search, patch.object(
            graph_module, "call_find_customer", return_value="customer result"
        ) as operations_search:
            result = await graph_module.graph.ainvoke(
                initial_state("What security requirements apply to Acme, and does Acme exist?")
            )

        knowledge_search.assert_awaited_once()
        operations_search.assert_awaited_once_with("Acme")
        self.assertEqual(
            "Acme exists and has documented security requirements.",
            result["messages"][-1].content,
        )


if __name__ == "__main__":
    unittest.main()
