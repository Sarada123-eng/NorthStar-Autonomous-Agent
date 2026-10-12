import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from sqlalchemy import text

import app.agent.graph as graph_module
from app.agent.mcp_client import get_invoice, list_invoices, log_invoice
from app.agent.state import RunStatus
from app.database import engine, initialize_database


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
            "lookup_result": {
                "found": True,
                "customer": {"id": 1, "legal_name": "Acme Corporation Inc."},
            },
        },
        "verification_result": None,
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


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, _messages):
        return next(self.responses)


class InvoiceManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM invoices WHERE invoice_number LIKE 'TASK22-%'")
            )

    async def test_seeded_current_invoice_is_active_final_revision(self):
        result = json.loads(await list_invoices("Acme Corporation Inc."))

        self.assertTrue(result["success"])
        self.assertEqual("INV-2026-1018-R1", result["current_invoice"]["invoice_number"])
        self.assertEqual("112500.00", result["current_invoice"]["amount"])
        self.assertEqual("2026-11-03", result["current_invoice"]["due_date"])
        self.assertEqual("active", result["current_invoice"]["status"])
        original = next(
            invoice for invoice in result["invoices"] if invoice["invoice_number"] == "INV-2026-1018"
        )
        self.assertEqual("superseded", original["status"])
        self.assertEqual(original["id"], result["current_invoice"]["supersedes_invoice_id"])

    async def test_log_invoice_and_read_back_through_invoice_mcp(self):
        created = json.loads(
            await log_invoice(
                "Globex",
                "TASK22-100",
                "99.50",
                "2026-12-01",
                "2026-11-01",
            )
        )
        stored = json.loads(await get_invoice(created["invoice_id"]))

        self.assertTrue(created["success"])
        self.assertEqual(created["invoice_id"], stored["invoice_id"])
        self.assertEqual(created["customer_id"], stored["customer_id"])
        self.assertEqual("99.50", stored["invoice"]["amount"])
        self.assertEqual("2026-12-01", stored["invoice"]["due_date"])

    async def test_successful_log_is_independently_verified(self):
        call = {
            "name": "log_invoice",
            "args": {
                "customer_name": "Acme Corporation Inc.",
                "invoice_number": "TASK22-200",
                "amount": "250.00",
                "due_date": "2026-12-15",
                "issued_date": "2026-11-15",
                "revision_number": 0,
                "status": "active",
                "supersedes_invoice_number": None,
            },
            "id": "log-invoice",
            "type": "tool_call",
        }
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[call]),
                AIMessage(content="The invoice was logged and verified."),
            ]
        )
        actual = {
            "id": 200,
            "customer_id": 1,
            "customer_name": "Acme Corporation Inc.",
            "invoice_number": "TASK22-200",
            "amount": "250.00",
            "due_date": "2026-12-15",
            "revision_number": 0,
            "status": "active",
            "supersedes_invoice_number": None,
        }
        with patch.object(graph_module, "get_llm", return_value=model), patch.object(
            graph_module,
            "call_log_invoice",
            AsyncMock(return_value=json.dumps({"success": True, "invoice_id": 200, "customer_id": 1})),
        ) as log_call, patch.object(
            graph_module,
            "call_get_invoice",
            AsyncMock(return_value=json.dumps({"success": True, "invoice": actual})),
        ) as get_call:
            result = await graph_module.graph.ainvoke(initial_state("Log an invoice for Acme."))

        log_call.assert_awaited_once_with(
            "Acme Corporation Inc.",
            "TASK22-200",
            "250.00",
            "2026-12-15",
            "2026-11-15",
            0,
            "active",
            None,
        )
        get_call.assert_awaited_once_with(200)
        self.assertEqual("PASS", result["invoice_verification_result"]["status"])
        self.assertEqual("The invoice was logged and verified.", result["messages"][-1].content)

    async def test_wrong_invoice_id_and_superseded_invoice_are_not_current(self):
        missing = json.loads(await get_invoice(999999999))
        invoices = json.loads(await list_invoices("Acme Corporation Inc."))

        self.assertFalse(missing["success"])
        self.assertEqual("not_found", missing["error_type"])
        self.assertNotEqual("INV-2026-1018", invoices["current_invoice"]["invoice_number"])


if __name__ == "__main__":
    unittest.main()
