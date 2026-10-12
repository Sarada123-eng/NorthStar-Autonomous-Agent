import unittest
from unittest.mock import patch
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
        self.seen_messages = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        self.seen_messages.append(messages)
        return next(self.responses)


class ClarificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_acme_interrupts_without_repeating_search(self):
        search_call = {
            "name": "search_documents",
            "args": {"query": "Acme legal entity identity"},
            "id": "search-1",
            "type": "tool_call",
        }
        clarification_call = {
            "name": "request_clarification",
            "args": {
                "question": (
                    "Which Acme do you mean: Acme Corporation Inc. "
                    "or Acme Corporation Ltd.?"
                )
            },
            "id": "clarify-1",
            "type": "tool_call",
        }
        follow_up_search = {
            "name": "search_documents",
            "args": {"query": "Acme Corporation Inc. onboarding identity"},
            "id": "search-2",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[search_call]),
                AIMessage(content="", tool_calls=[clarification_call]),
                AIMessage(content="", tool_calls=[follow_up_search]),
                AIMessage(
                    content=(
                        "I will continue onboarding Acme Corporation Inc. "
                        "using the clarification you provided."
                    )
                ),
            ]
        )
        state = initial_state("Onboard Acme")
        config = {"configurable": {"thread_id": state["run_id"]}}

        with patch.object(graph_module, "get_llm", return_value=model):
            paused = await graph_module.graph.ainvoke(state, config=config)

            self.assertIn("__interrupt__", paused)
            interrupt_value = paused["__interrupt__"][0].value
            self.assertEqual("clarification", interrupt_value["type"])
            self.assertIn("Acme Corporation Inc.", interrupt_value["question"])
            self.assertIn("Acme Corporation Ltd.", interrupt_value["question"])

            result = await graph_module.graph.ainvoke(
                Command(resume="Acme Corporation Inc."),
                config=config,
            )

        self.assertEqual(
            "I will continue onboarding Acme Corporation Inc. "
            "using the clarification you provided.",
            result["messages"][-1].content,
        )
        self.assertEqual("Acme Corporation Inc.", result["human_response"])
        self.assertEqual(2, result["tool_step_count"])
        self.assertEqual(
            ["Acme legal entity identity", "Acme Corporation Inc. onboarding identity"],
            [
                call["args"]["query"]
                for call in (search_call, follow_up_search)
            ],
        )
        self.assertTrue(
            any(
                "Acme legal entity identity" in str(message)
                for message in model.seen_messages[-1]
            )
        )

    async def test_clarification_uses_real_knowledge_mcp_boundary(self):
        model = FakeModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_documents",
                            "args": {"query": "Onboard Acme"},
                            "id": "search-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "request_clarification",
                            "args": {
                                "question": (
                                    "Which Acme legal entity should be onboarded?"
                                )
                            },
                            "id": "clarify-1",
                            "type": "tool_call",
                        }
                    ],
                ),
            ]
        )
        state = initial_state("Onboard Acme")
        config = {"configurable": {"thread_id": state["run_id"]}}

        with patch.object(graph_module, "get_llm", return_value=model):
            paused = await graph_module.graph.ainvoke(state, config=config)

        self.assertIn("__interrupt__", paused)
        self.assertGreater(len(paused["messages"]), 2)
        self.assertIn("Acme", paused["messages"][2].content)


if __name__ == "__main__":
    unittest.main()
