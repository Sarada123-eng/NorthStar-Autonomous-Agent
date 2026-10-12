import json
import sys
from pathlib import Path

from fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import get_invoice as query_invoice
from app.database import list_invoices as query_invoices
from app.database import log_invoice as insert_invoice

mcp = FastMCP("Northstar Invoice Server")


@mcp.tool
async def list_invoices(customer_name: str) -> str:
    """List a customer's invoices with active/superseded revision relationships."""
    return json.dumps(await query_invoices(customer_name), default=str)


@mcp.tool
async def get_invoice(invoice_id: int) -> str:
    """Retrieve one persisted invoice by its invoice_id."""
    return json.dumps(await query_invoice(invoice_id), default=str)


@mcp.tool
async def log_invoice(
    customer_name: str,
    invoice_number: str,
    amount: str,
    due_date: str,
    issued_date: str,
    revision_number: int = 0,
    status: str = "active",
    supersedes_invoice_number: str | None = None,
) -> str:
    """Create or update a customer invoice with its revision relationship."""
    return json.dumps(
        await insert_invoice(
            customer_name,
            invoice_number,
            amount,
            due_date,
            issued_date,
            revision_number,
            status,
            supersedes_invoice_number,
        ),
        default=str,
    )


if __name__ == "__main__":
    mcp.run()
