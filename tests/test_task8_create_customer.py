import json
import unittest

from sqlalchemy import text

from app.agent.mcp_client import create_customer
from app.database import engine, initialize_database


class CreateCustomerMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_database()

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM customers WHERE name = 'Task8TestCustomer'")
            )

    async def test_customer_is_created_and_persisted(self):
        result = json.loads(
            await create_customer(
                "Task8TestCustomer",
                "billing@task8.example",
                "tech@task8.example",
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual("onboarding", result["customer"]["status"])

        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT name, billing_contact, technical_contact "
                        "FROM customers WHERE name = :name"
                    ),
                    {"name": "Task8TestCustomer"},
                )
            ).mappings().one()
        self.assertEqual("billing@task8.example", row["billing_contact"])
        self.assertEqual("tech@task8.example", row["technical_contact"])

    async def test_duplicate_creation_returns_structured_failure(self):
        result = json.loads(
            await create_customer(
                "Acme",
                "new-billing@acme.example",
                "new-tech@acme.example",
            )
        )

        self.assertFalse(result["success"])
        self.assertEqual("duplicate", result["error_type"])

        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT COUNT(*) FROM customers WHERE name = 'Acme'")
                )
            ).scalar_one()
        self.assertEqual(1, count)


if __name__ == "__main__":
    unittest.main()
