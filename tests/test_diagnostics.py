import os
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.diagnostics import build_diagnostic_report, diagnostics_enabled, emit_diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_report_redacts_sensitive_content_and_email_addresses(self):
        state = {
            "run_id": "run-1",
            "messages": [
                AIMessage(
                    content="private reasoning is intentionally not recorded",
                    tool_calls=[
                        {
                            "name": "send_email",
                            "args": {"to": "person@example.com", "body": "password=secret"},
                            "id": "mail-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content='{"success": true, "email": {"body": "secret", "recipient": "person@example.com"}}',
                    tool_call_id="mail-1",
                ),
            ],
            "execution_stop_reason": None,
            "verification_result": None,
            "onboarding_verification_result": {"status": "PASS", "kind": "project"},
            "invoice_verification_result": None,
        }

        report = build_diagnostic_report(state, [{"event": "interrupt", "type": "approval"}])

        self.assertEqual("run-1", report["run_id"])
        self.assertEqual("p***@example.com", report["events"][0]["arguments"]["to"])
        self.assertEqual("[redacted]", report["events"][0]["arguments"]["body"])
        self.assertEqual("[redacted]", report["events"][1]["result"]["email"]["body"])
        self.assertEqual("PASS", report["onboarding_verification_result"]["status"])
        self.assertNotIn("private reasoning", str(report))

    def test_report_omits_retrieved_document_content(self):
        state = {
            "run_id": "run-3",
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_documents",
                            "args": {"query": "policy"},
                            "id": "search-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content=(
                        '[{"source": "company/policy.md", '
                        '"content": "confidential procedure"}]'
                    ),
                    tool_call_id="search-1",
                ),
            ],
        }

        report = build_diagnostic_report(state, [])

        self.assertEqual("[redacted]", report["events"][1]["result"][0]["content"])
        self.assertNotIn("confidential procedure", str(report))

    def test_diagnostics_are_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.print") as output:
            self.assertFalse(diagnostics_enabled())
            self.assertFalse(emit_diagnostics({"run_id": "run-2"}, []))
        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
