import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

import api.main as api_module


class FakeGraph:
    def __init__(self, *, interaction: str | None = None, verified: bool = True):
        self.interaction = interaction
        self.verified = verified
        self.resumes = []
        self.state = {}

    async def astream(self, graph_input, *, config, stream_mode):
        assert stream_mode == "updates"
        assert config["configurable"]["thread_id"] == self.state["run_id"]
        if self.interaction and not isinstance(graph_input, Command):
            payload = (
                {"type": "clarification", "question": "Which legal entity?"}
                if self.interaction == "clarification"
                else {
                    "type": "approval",
                    "tool": "send_email",
                    "arguments": {"to": "outside@example.com"},
                    "reason": "Approval required.",
                }
            )
            yield {
                "decide": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "request_clarification",
                                    "args": {"question": "Which legal entity?"},
                                    "id": "clarify-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            }
            yield {"__interrupt__": (payload,)}
            return
        if isinstance(graph_input, Command):
            self.resumes.append(graph_input)
        yield {
            "decide": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "create_project",
                                "args": {"customer_name": "Acme Corporation Inc.", "name": "UI test"},
                                "id": "project-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        }
        yield {
            "execute_tool": {
                "messages": [
                    ToolMessage(
                        content=json.dumps({"success": True, "project": {"id": 42}}),
                        tool_call_id="project-1",
                    )
                ]
            }
        }
        evidence = {"status": "PASS", "writes": [{"kind": "project", "project_id": 42}]} if self.verified else None
        yield {"verify_onboarding": {"onboarding_verification_result": evidence}}
        self.state["onboarding_verification_result"] = evidence
        self.state["messages"] = [AIMessage(content="Graph returned an actual final response.")]

    async def aget_state(self, config):
        assert config["configurable"]["thread_id"] == self.state["run_id"]
        return SimpleNamespace(values=self.state)


class UiApiTests(unittest.TestCase):
    def setUp(self):
        api_module.runs.clear()
        api_module.submissions.clear()

    def test_frontend_subscribes_to_evidence_events(self):
        source = (Path(__file__).parents[1] / "ui" / "app.js").read_text()
        self.assertIn('"evidence"', source)

    def _client_for(self, fake):
        original = api_module.graph
        fake.state = api_module._initial_state("Create a UI test project.", "pending")

        class ConfiguredFake(FakeGraph):
            async def astream(self, graph_input, *, config, stream_mode):
                self.state["run_id"] = config["configurable"]["thread_id"]
                async for update in super().astream(graph_input, config=config, stream_mode=stream_mode):
                    yield update

        replacement = ConfiguredFake(interaction=fake.interaction, verified=fake.verified)
        return patch.object(api_module, "graph", replacement), replacement, original

    def test_retrieval_result_emits_actual_evidence_separately(self):
        record = api_module.RunRecord("run-evidence", "Find policy evidence.", "submission-evidence")
        update = {
            "execute_tool": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{
                            "name": "search_documents",
                            "args": {"query": "Delta Health onboarding"},
                            "id": "search-1",
                            "type": "tool_call",
                        }],
                    ),
                    ToolMessage(
                        content=json.dumps([{
                            "source": "contracts/delta_health_2026.md",
                            "score": 4,
                            "content": "The customer requires a security review before launch.",
                        }]),
                        tool_call_id="search-1",
                    ),
                ]
            }
        }

        api_module._record_messages(record, update["execute_tool"])

        evidence = [event for event in record.events if event["type"] == "evidence"]
        self.assertEqual(1, len(evidence))
        self.assertEqual("search_documents", evidence[0]["payload"]["tool"])
        self.assertEqual(
            "The customer requires a security review before launch.",
            evidence[0]["payload"]["sources"][0]["excerpt"],
        )

    def test_failed_or_empty_retrieval_emits_no_evidence(self):
        record = api_module.RunRecord("run-no-evidence", "Find evidence.", "submission-no-evidence")
        update = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "search_documents",
                        "args": {"query": "missing"},
                        "id": "search-2",
                        "type": "tool_call",
                    }],
                ),
                ToolMessage(
                    content=json.dumps({"success": False, "error_type": "not_found"}),
                    tool_call_id="search-2",
                ),
            ]
        }

        api_module._record_messages(record, update)

        self.assertNotIn("evidence", [event["type"] for event in record.events])

    def test_verification_retrieval_emits_actual_evidence_once(self):
        record = api_module.RunRecord("run-verification-evidence", "Onboard Delta.", "submission")
        payload = {
            "verify_requirements": {
                "verification_evidence": [
                    {"source": "contracts/delta.md", "excerpt": "Actual contract text."},
                    {"source": "company/onboarding_sop.md", "excerpt": "Actual SOP text."},
                ]
            }
        }

        api_module._record_update(record, payload)
        api_module._record_update(record, payload)

        evidence = [event for event in record.events if event["type"] == "evidence"]
        self.assertEqual(1, len(evidence))
        self.assertEqual(
            ["contracts/delta.md", "company/onboarding_sop.md"],
            [item["source"] for item in evidence[0]["payload"]["sources"]],
        )
        self.assertEqual("Actual contract text.", evidence[0]["payload"]["sources"][0]["excerpt"])

    @staticmethod
    def _wait(client, run_id, expected):
        for _ in range(50):
            response = client.get(f"/api/runs/{run_id}")
            if response.json()["status"] == expected:
                return response.json()
            time.sleep(0.01)
        raise AssertionError(f"Run did not reach {expected}")

    def test_ui_is_served_and_verified_run_exposes_real_evidence(self):
        patcher, _, _ = self._client_for(FakeGraph(verified=True))
        with patcher, TestClient(api_module.app) as client:
            self.assertEqual(200, client.get("/").status_code)
            started = client.post("/api/runs", json={"goal": "Create a UI test project.", "submission_id": "submission-verified"})
            self.assertEqual(202, started.status_code)
            run_id = started.json()["run_id"]
            report = self._wait(client, run_id, "completed")
            self.assertEqual("verified", report["outcome"])
            self.assertEqual("PASS", report["verification"]["onboarding_writes"]["status"])
            event_types = [event["type"] for event in api_module.runs[run_id].events]
            self.assertIn("tool_selected", event_types)
            self.assertIn("tool_result", event_types)
            self.assertIn("verification", event_types)
            with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
                self.assertEqual(200, stream.status_code)
                trace = b"".join(stream.iter_bytes()).decode()
            self.assertIn("event: tool_selected", trace)
            self.assertIn("event: verification", trace)
            duplicate = client.post("/api/runs", json={"goal": "Create a UI test project.", "submission_id": "submission-verified"})
            self.assertTrue(duplicate.json()["reused_submission"])
            self.assertEqual(run_id, duplicate.json()["run_id"])

    def test_backend_initializes_execution_guard_state(self):
        """The UI must pass the same unset guard state the CLI supplies."""

        class StateCaptureGraph:
            def __init__(self):
                self.state = {}

            async def astream(self, graph_input, *, config, stream_mode):
                assert stream_mode == "updates"
                assert config["configurable"]["thread_id"] == graph_input["run_id"]
                # This is the same direct state access execute_tool() makes
                # before it evaluates the selected tool action.
                assert graph_input["last_failed_non_retryable_action"] is None
                self.state = dict(graph_input)
                self.state["messages"] = [AIMessage(content="Initialization reached graph execution.")]
                yield {"decide": {"messages": self.state["messages"]}}

            async def aget_state(self, config):
                assert config["configurable"]["thread_id"] == self.state["run_id"]
                return SimpleNamespace(values=self.state)

        capture = StateCaptureGraph()
        with patch.object(api_module, "graph", capture), TestClient(api_module.app) as client:
            started = client.post(
                "/api/runs",
                json={"goal": "Inspect UI initialization.", "submission_id": "submission-guard-state"},
            )
            self.assertEqual(202, started.status_code)
            report = self._wait(client, started.json()["run_id"], "completed")

        self.assertEqual("Initialization reached graph execution.", report["summary"])
        self.assertIn("last_failed_non_retryable_action", capture.state)
        self.assertIsNone(capture.state["last_failed_non_retryable_action"])

    def test_unverified_completion_is_not_presented_as_verified(self):
        patcher, _, _ = self._client_for(FakeGraph(verified=False))
        with patcher, TestClient(api_module.app) as client:
            started = client.post("/api/runs", json={"goal": "Create a UI test project.", "submission_id": "submission-unverified"})
            report = self._wait(client, started.json()["run_id"], "completed")
            self.assertEqual("unverified", report["outcome"])

    def test_clarification_resumes_the_same_run(self):
        patcher, fake, _ = self._client_for(FakeGraph(interaction="clarification"))
        with patcher, TestClient(api_module.app) as client:
            started = client.post("/api/runs", json={"goal": "Onboard Acme.", "submission_id": "submission-clarify"})
            run_id = started.json()["run_id"]
            paused = self._wait(client, run_id, "awaiting_clarification")
            self.assertEqual("clarification", paused["pending"]["type"])
            resumed = client.post(f"/api/runs/{run_id}/clarification", json={"response": "Acme Corporation Inc."})
            self.assertEqual(202, resumed.status_code)
            self._wait(client, run_id, "completed")
            self.assertEqual(1, len(fake.resumes))
            self.assertEqual("Acme Corporation Inc.", fake.resumes[0].resume)

    def test_consumed_clarification_does_not_restore_historical_pending_state(self):
        class HistoricalClarificationGraph:
            def __init__(self):
                self.state = {}
                self.resumes = []

            async def astream(self, graph_input, *, config, stream_mode):
                assert stream_mode == "updates"
                if not isinstance(graph_input, Command):
                    clarification = AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "request_clarification",
                                "args": {"question": "Which Acme legal entity?"},
                                "id": "clarify-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                    self.state = dict(graph_input)
                    self.state["messages"] = [clarification]
                    self.state["pending_human_input"] = "Which Acme legal entity?"
                    self.state["pending_human_input_type"] = "clarification"
                    yield {"decide": {"messages": [clarification]}}
                    yield {"__interrupt__": ({"type": "clarification", "question": "Which Acme legal entity?"},)}
                    return

                self.resumes.append(graph_input.resume)
                self.state["pending_human_input"] = None
                self.state["pending_human_input_type"] = None
                self.state["human_response"] = graph_input.resume
                self.state["onboarding_completion_result"] = {
                    "status": "PASS", "attempt": 2, "missing": []
                }
                self.state["messages"].extend(
                    [
                        AIMessage(content=""),
                        AIMessage(content="Onboarding completed after clarification."),
                    ]
                )
                yield {"execute_tool": {"messages": []}}
                yield {
                    "verify_requirements": {
                        "onboarding_completion_result": self.state["onboarding_completion_result"]
                    }
                }

            async def aget_state(self, config):
                return SimpleNamespace(values=self.state)

        graph = HistoricalClarificationGraph()
        with patch.object(api_module, "graph", graph), TestClient(api_module.app) as client:
            started = client.post(
                "/api/runs",
                json={"goal": "Onboard Acme.", "submission_id": "submission-consumed-clarification"},
            )
            run_id = started.json()["run_id"]
            self._wait(client, run_id, "awaiting_clarification")
            resumed = client.post(
                f"/api/runs/{run_id}/clarification",
                json={"response": "Acme Corporation Inc."},
            )
            self.assertEqual(202, resumed.status_code)
            report = self._wait(client, run_id, "completed")
            repeated = client.post(
                f"/api/runs/{run_id}/clarification",
                json={"response": "Acme Corporation Inc."},
            )

        self.assertEqual("verified", report["outcome"])
        self.assertIsNone(report["pending"])
        self.assertEqual(["Acme Corporation Inc."], graph.resumes)
        self.assertEqual(409, repeated.status_code)
        events = api_module.runs[run_id].events
        self.assertEqual(1, sum(event["type"] == "human_input_required" for event in events))

    def test_genuine_pending_clarification_remains_awaiting(self):
        patcher, _, _ = self._client_for(FakeGraph(interaction="clarification"))
        with patcher, TestClient(api_module.app) as client:
            started = client.post(
                "/api/runs",
                json={"goal": "Onboard Acme.", "submission_id": "submission-pending-clarification"},
            )
            report = self._wait(client, started.json()["run_id"], "awaiting_clarification")

        self.assertEqual("clarification", report["pending"]["type"])

    def test_approval_resumes_the_same_run(self):
        patcher, fake, _ = self._client_for(FakeGraph(interaction="approval"))
        with patcher, TestClient(api_module.app) as client:
            started = client.post("/api/runs", json={"goal": "Send an update.", "submission_id": "submission-approval"})
            run_id = started.json()["run_id"]
            paused = self._wait(client, run_id, "awaiting_approval")
            self.assertEqual("send_email", paused["pending"]["tool"])
            resumed = client.post(f"/api/runs/{run_id}/approval", json={"decision": "approve"})
            self.assertEqual(202, resumed.status_code)
            self._wait(client, run_id, "completed")
            self.assertEqual("approve", fake.resumes[0].resume)


class ReportOutcomeTests(unittest.TestCase):
    def report_for(self, completion):
        record = api_module.RunRecord("run-1", "Onboard Acme Corp", "submission-1")
        record.status = "completed"
        record.state = {
            "messages": [AIMessage(content="Done.")],
            "onboarding_verification_result": {"status": "PASS", "writes": []},
            "onboarding_completion_result": completion,
        }
        return api_module._report(record)

    def test_passing_writes_without_a_passing_completion_check_are_not_verified(self):
        self.assertEqual("unverified", self.report_for({"status": "SKIPPED", "reason": "checker unavailable"})["outcome"])
        self.assertEqual("unverified", self.report_for({"status": "FAIL", "missing": ["Capacity review"]})["outcome"])

    def test_passing_writes_and_completion_are_verified(self):
        self.assertEqual("verified", self.report_for({"status": "PASS"})["outcome"])

    def test_runs_without_a_completion_check_keep_their_previous_outcome(self):
        self.assertEqual("verified", self.report_for(None)["outcome"])

    def test_failed_invoice_reconciliation_is_not_presented_as_verified(self):
        record = api_module.RunRecord("run-invoice", "Verify invoice", "submission-invoice")
        record.status = "completed"
        record.state = {
            "messages": [AIMessage(content="Invoice check finished.")],
            "onboarding_verification_result": {"status": "PASS", "writes": []},
            "invoice_reconciliation_result": {"status": "FAILED", "fields": []},
        }
        self.assertEqual("unverified", api_module._report(record)["outcome"])


class UiEventCursorTests(unittest.TestCase):
    def setUp(self):
        api_module.runs.clear()
        api_module.submissions.clear()

    def test_event_cursor_excludes_historical_events(self):
        record = api_module.RunRecord("run-events", "Onboard Acme", "submission-events")
        record.status = "completed"
        api_module.runs[record.run_id] = record
        api_module._event(record, "run_started", {"run_id": record.run_id})
        api_module._event(record, "node_update", {"node": "decide"})
        api_module._event(record, "run_status", {"status": "completed"})

        with TestClient(api_module.app) as client:
            with client.stream("GET", f"/api/runs/{record.run_id}/events?after=2") as stream:
                replay = b"".join(stream.iter_bytes()).decode()

        self.assertIn('"sequence": 3', replay)
        self.assertNotIn('"sequence": 1', replay)
        self.assertNotIn('"sequence": 2', replay)

    def test_ui_reconnects_from_the_last_seen_event(self):
        script = (Path(__file__).parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("after=${lastEventSequence}", script)
        self.assertIn("event.sequence <= lastEventSequence", script)


if __name__ == "__main__":
    unittest.main()
