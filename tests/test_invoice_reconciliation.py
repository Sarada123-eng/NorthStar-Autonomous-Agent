import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

import app.agent.graph as graph_module
from app.agent.state import RunStatus


def call(name, args, identifier):
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


DOCUMENT = """| **Invoice Number** | INV-2026-1018-R1 |
| **Revision Status** | Active / Final (Supersedes INV-2026-1018) |
| **Payment Due Date** | November 3, 2026 |
| **Customer Legal Name** | Acme Corporation Inc. |
| **Total Amount Due** | **$112,500.00** |"""


def invoice(invoice_id=102, amount="112500.00", status="active", supersedes="INV-2026-1018"):
    return {
        "id": invoice_id,
        "customer_id": 1,
        "customer_name": "Acme Corporation Inc.",
        "customer_short_name": "Acme",
        "invoice_number": "INV-2026-1018-R1",
        "amount": amount,
        "due_date": "2026-11-03",
        "issued_date": "2026-10-04",
        "revision_number": 1,
        "supersedes_invoice_number": supersedes,
        "status": status,
    }


def reconciliation_state(selected=None, current=None):
    run_id = str(uuid4())
    selected = selected or invoice()
    current = current or dict(selected)
    list_result = {
        "success": True,
        "invoices": [current],
        "current_invoice": current,
    }
    get_result = {"success": True, "invoice": selected, "invoice_id": selected["id"]}
    return {
        "run_id": run_id,
        "goal": "Verify and reconcile the latest invoice for Acme Corporation Inc.",
        "messages": [
            AIMessage(content="", tool_calls=[call("list_invoices", {"customer_name": "Acme Corporation Inc."}, "list")]),
            ToolMessage(content=json.dumps(list_result), tool_call_id="list"),
            AIMessage(content="", tool_calls=[call("get_invoice", {"invoice_id": selected["id"]}, "get")]),
            ToolMessage(content=json.dumps(get_result), tool_call_id="get"),
        ],
        "status": RunStatus.RUNNING,
        "plan": [], "current_action": None, "retrieved_context": [],
        "last_tool_result": {"tools": [{"tool": "get_invoice", "content": json.dumps(get_result)}]},
        "last_tool_succeeded": True,
        "pending_human_input": None, "pending_human_input_type": None,
        "human_response": None, "pending_approval_action": None,
        "customer_resolution": None,
        "verification_result": None, "onboarding_verification_result": None,
        "invoice_verification_result": None, "invoice_reconciliation_result": None,
        "verification_repair_count": 0, "business_checks": False,
        "completion_check_count": 0, "onboarding_completion_result": None,
        "tool_step_count": 2, "retry_counts": {}, "last_action_signature": None,
        "consecutive_action_count": 0, "last_failed_non_retryable_action": None,
        "execution_stop_reason": None, "error": None,
    }


class InvoiceReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def reconcile(self, state, persisted=None, document=DOCUMENT):
        persisted = persisted if persisted is not None else invoice()
        with patch.object(
            graph_module, "call_get_invoice", AsyncMock(return_value=json.dumps({"success": True, "invoice": persisted}))
        ) as get_invoice, patch.object(
            graph_module, "call_search_documents", AsyncMock(return_value=json.dumps([{"source": "invoices/acme_invoice.md"}]))
        ) as search, patch.object(
            graph_module, "call_read_document", AsyncMock(return_value=json.dumps({"success": True, "content": document}))
        ) as read, patch.object(graph_module, "call_log_invoice", AsyncMock()) as log:
            update = await graph_module.reconcile_invoice(state)
        return update, get_invoice, search, read, log

    async def test_matching_active_invoice_is_verified_without_writes(self):
        update, get_invoice, search, read, log = await self.reconcile(reconciliation_state())

        evidence = update["invoice_reconciliation_result"]
        self.assertEqual("VERIFIED", evidence["status"])
        self.assertTrue(all(field["matched"] for field in evidence["fields"]))
        self.assertIsNone(update["execution_stop_reason"])
        get_invoice.assert_awaited_once_with(102)
        search.assert_awaited_once_with("INV-2026-1018-R1 invoice")
        read.assert_awaited_once_with("invoices/acme_invoice.md")
        log.assert_not_awaited()

    async def test_field_mismatch_fails_and_cannot_be_a_verified_outcome(self):
        update, _, _, _, log = await self.reconcile(
            reconciliation_state(), persisted=invoice(amount="100.00")
        )

        evidence = update["invoice_reconciliation_result"]
        self.assertEqual("FAILED", evidence["status"])
        self.assertEqual("invoice_reconciliation_failed", update["execution_stop_reason"])
        self.assertFalse(next(field for field in evidence["fields"] if field["field"] == "amount")["matched"])
        log.assert_not_awaited()

    async def test_missing_persisted_record_is_insufficient_evidence(self):
        state = reconciliation_state()
        with patch.object(
            graph_module, "call_get_invoice", AsyncMock(return_value=json.dumps({"success": False, "error_type": "not_found"}))
        ), patch.object(graph_module, "call_log_invoice", AsyncMock()) as log:
            update = await graph_module.reconcile_invoice(state)

        self.assertEqual("INSUFFICIENT_EVIDENCE", update["invoice_reconciliation_result"]["status"])
        self.assertEqual("invoice_reconciliation_insufficient_evidence", update["execution_stop_reason"])
        log.assert_not_awaited()

    async def test_superseded_or_wrong_selected_revision_fails(self):
        current = invoice(invoice_id=102)
        selected = invoice(invoice_id=101, status="superseded", supersedes=None)
        update, _, _, _, _ = await self.reconcile(
            reconciliation_state(selected=selected, current=current), persisted=selected
        )

        evidence = update["invoice_reconciliation_result"]
        revision = next(field for field in evidence["fields"] if field["field"] == "current_revision")
        self.assertEqual("FAILED", evidence["status"])
        self.assertFalse(revision["matched"])

    async def test_missing_document_fields_are_insufficient_evidence(self):
        update, _, _, _, _ = await self.reconcile(reconciliation_state(), document="| **Invoice Number** | INV-2026-1018-R1 |")

        self.assertEqual("INSUFFICIENT_EVIDENCE", update["invoice_reconciliation_result"]["status"])

    async def test_failed_reconciliation_final_response_reports_actual_mismatch(self):
        state = reconciliation_state()
        state["invoice_reconciliation_result"] = {
            "status": "FAILED",
            "source": "invoices/acme_invoice_2026_10_revised.md",
            "invoice_id": 102,
            "fields": [
                {
                    "field": "customer_name",
                    "expected": "Acme Corporation Inc.",
                    "actual": "Acme Corporation",
                    "matched": False,
                }
            ],
        }
        state["execution_stop_reason"] = "invoice_reconciliation_failed"

        with patch.object(graph_module, "get_llm") as get_llm:
            update = await graph_module.answer(state)

        get_llm.assert_not_called()
        content = update["messages"][0].content
        self.assertIn("Invoice reconciliation FAILED.", content)
        self.assertIn("customer_name: FAIL", content)
        self.assertIn("expected: Acme Corporation Inc.", content)
        self.assertIn("actual: Acme Corporation", content)
        self.assertIn("Execution stop reason: invoice_reconciliation_failed", content)
        self.assertNotIn("Invoice reconciliation VERIFIED.", content)

    async def test_insufficient_evidence_final_response_never_claims_verification(self):
        state = reconciliation_state()
        state["invoice_reconciliation_result"] = {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "authoritative_invoice_document_unavailable",
            "fields": [],
        }
        state["execution_stop_reason"] = "invoice_reconciliation_insufficient_evidence"

        update = await graph_module.answer(state)

        content = update["messages"][0].content
        self.assertIn("INSUFFICIENT_EVIDENCE", content)
        self.assertIn("authoritative_invoice_document_unavailable", content)
        self.assertNotIn("Invoice reconciliation VERIFIED.", content)

    async def test_verified_reconciliation_final_response_reports_checked_values(self):
        state = reconciliation_state()
        state["invoice_reconciliation_result"] = {
            "status": "VERIFIED",
            "source": "invoices/acme_invoice_2026_10_revised.md",
            "invoice_id": 102,
            "fields": [
                {
                    "field": "amount",
                    "expected": "112500.00",
                    "actual": "112500.00",
                    "matched": True,
                }
            ],
        }

        update = await graph_module.answer(state)

        content = update["messages"][0].content
        self.assertIn("Invoice reconciliation VERIFIED.", content)
        self.assertIn("amount: PASS", content)
        self.assertIn("expected: 112500.00", content)
        self.assertIn("actual: 112500.00", content)

    async def test_graph_routes_read_only_invoice_workflow_to_reconciliation(self):
        class FakeModel:
            def __init__(self):
                self.responses = iter(
                    [
                        AIMessage(content="", tool_calls=[call("list_invoices", {"customer_name": "Acme Corporation Inc."}, "list")]),
                        AIMessage(content="", tool_calls=[call("get_invoice", {"invoice_id": 102}, "get")]),
                        AIMessage(content="The active invoice was reconciled against the available internal document."),
                    ]
                )

            def bind_tools(self, tools):
                del tools
                return self

            async def ainvoke(self, messages):
                del messages
                return next(self.responses)

        state = reconciliation_state()
        state["messages"] = []
        state["customer_resolution"] = {
            "status": "resolved",
            "reference": "Acme Corporation Inc.",
            "normalized_reference": "acme corporation inc",
            "run_id": state["run_id"],
            "customer": {"id": 1, "legal_name": "Acme Corporation Inc."},
        }
        listed = {"success": True, "invoices": [invoice()], "current_invoice": invoice()}
        selected = {"success": True, "invoice": invoice(), "invoice_id": 102}
        with patch.object(graph_module, "get_llm", return_value=FakeModel()), patch.object(
            graph_module, "call_list_invoices", AsyncMock(return_value=json.dumps(listed))
        ) as list_invoices, patch.object(
            graph_module, "call_get_invoice", AsyncMock(return_value=json.dumps(selected))
        ) as get_invoice, patch.object(
            graph_module, "call_search_documents", AsyncMock(return_value=json.dumps([{"source": "invoices/acme_invoice.md"}]))
        ), patch.object(
            graph_module, "call_read_document", AsyncMock(return_value=json.dumps({"success": True, "content": DOCUMENT}))
        ), patch.object(graph_module, "call_log_invoice", AsyncMock()) as log:
            result = await graph_module.graph.ainvoke(
                state, {"configurable": {"thread_id": state["run_id"]}}
            )

        list_invoices.assert_awaited_once_with("Acme Corporation Inc.")
        self.assertEqual(2, get_invoice.await_count)  # selection plus independent read-back
        log.assert_not_awaited()
        self.assertEqual("VERIFIED", result["invoice_reconciliation_result"]["status"])
        self.assertIsNone(result["execution_stop_reason"])
        self.assertIn("Invoice reconciliation VERIFIED.", result["messages"][-1].content)


if __name__ == "__main__":
    unittest.main()
