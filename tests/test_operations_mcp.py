import json
import unittest

from app.agent.mcp_client import find_customer
from app.database import initialize_database


class OperationsMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()

    async def test_seeded_customer_is_returned(self):
        result = json.loads(await find_customer("Acme"))

        self.assertTrue(result["found"])
        self.assertEqual("Acme", result["customer"]["name"])
        self.assertEqual("onboarding", result["customer"]["status"])

    async def test_unknown_customer_returns_not_found(self):
        result = json.loads(await find_customer("Unknown Company"))

        self.assertFalse(result["found"])
        self.assertIsNone(result["customer"])

    async def test_globex_contract_legal_name_resolves_after_short_name_clarification(self):
        """The seeded customer record must match the executed Globex contract."""
        short_name_result = json.loads(await find_customer("Globex"))
        legal_name_result = json.loads(
            await find_customer("Globex Corporation LLC")
        )

        self.assertEqual("ambiguous", short_name_result["resolution"]["status"])
        self.assertEqual(
            ["Globex Corporation LLC"],
            short_name_result["resolution"]["candidates"],
        )
        self.assertEqual("resolved", legal_name_result["resolution"]["status"])
        self.assertEqual(2, legal_name_result["customer"]["id"])
        self.assertEqual(
            "Globex Corporation LLC", legal_name_result["customer"]["legal_name"]
        )


if __name__ == "__main__":
    unittest.main()
