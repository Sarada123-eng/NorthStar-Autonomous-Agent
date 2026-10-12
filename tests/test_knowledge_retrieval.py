import json
import unittest

from app.agent.mcp_client import search_documents


class KnowledgeMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_queries_return_relevant_sources_or_no_results(self):
        cases = [
            ("Onboard Acme", "company/onboarding_sop.md"),
            ("What security requirements apply to Acme?", "company/security_policy.md"),
            ("Find the latest invoice issued to Acme", "invoices/acme_invoice_2026_10_revised.md"),
        ]

        for query, expected_source in cases:
            with self.subTest(query=query):
                results = json.loads(await search_documents(query))
                self.assertTrue(results)
                self.assertIn(expected_source, {result["source"] for result in results})

        self.assertEqual(
            [],
            json.loads(await search_documents("quantum bicycle parking on Mars")),
        )


if __name__ == "__main__":
    unittest.main()
