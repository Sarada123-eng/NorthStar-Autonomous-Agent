import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

SERVER_PATH = Path(__file__).parents[2] / "mcp_servers" / "knowledge_server.py"
OPERATIONS_SERVER_PATH = Path(__file__).parents[2] / "mcp_servers" / "operations_server.py"
COMMUNICATION_SERVER_PATH = (
    Path(__file__).parents[2] / "mcp_servers" / "communication_server.py"
)
INVOICE_SERVER_PATH = Path(__file__).parents[2] / "mcp_servers" / "invoice_server.py"


def _client(server_path: Path) -> Client:
    transport = StdioTransport(
        command=sys.executable,
        args=[str(server_path)],
        env=dict(os.environ),
    )
    return Client(transport)


async def search_documents(query: str) -> str:
    """Discover and call the knowledge MCP tool over local stdio."""
    async with _client(SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "search_documents" for tool in tools):
            raise RuntimeError("Knowledge MCP server did not expose search_documents")

        result = await client.call_tool("search_documents", {"query": query})
        if result.is_error:
            raise RuntimeError(f"Knowledge MCP tool failed: {result}")
        return result.content[0].text


async def read_document(source: str) -> str:
    """Read one whole knowledge document through the Knowledge MCP server."""
    async with _client(SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "read_document" for tool in tools):
            raise RuntimeError("Knowledge MCP server did not expose read_document")

        result = await client.call_tool("read_document", {"source": source})
        if result.is_error:
            raise RuntimeError(f"Knowledge MCP tool failed: {result}")
        return result.content[0].text


async def find_customer(name: str) -> str:
    """Discover and call the customer lookup tool over local stdio."""
    async with _client(OPERATIONS_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "find_customer" for tool in tools):
            raise RuntimeError("Operations MCP server did not expose find_customer")

        result = await client.call_tool("find_customer", {"name": name})
        if result.is_error:
            raise RuntimeError(f"Operations MCP tool failed: {result}")
        return result.content[0].text


async def find_customer_by_contact(recipient: str) -> str:
    """Find a customer by a listed communication contact over local stdio."""
    async with _client(OPERATIONS_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "find_customer_by_contact" for tool in tools):
            raise RuntimeError(
                "Operations MCP server did not expose find_customer_by_contact"
            )
        result = await client.call_tool(
            "find_customer_by_contact", {"recipient": recipient}
        )
        if result.is_error:
            raise RuntimeError(f"Operations MCP tool failed: {result}")
        return result.content[0].text


async def create_customer(
    name: str,
    billing_contact: str,
    technical_contact: str,
) -> str:
    """Discover and call the customer creation tool over local stdio."""
    async with _client(OPERATIONS_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "create_customer" for tool in tools):
            raise RuntimeError("Operations MCP server did not expose create_customer")

        result = await client.call_tool(
            "create_customer",
            {
                "name": name,
                "billing_contact": billing_contact,
                "technical_contact": technical_contact,
            },
        )
        if result.is_error:
            raise RuntimeError(f"Operations MCP tool failed: {result}")
        return result.content[0].text


async def list_projects(customer_name: str) -> str:
    """List customer projects through the Operations MCP server."""
    async with _client(OPERATIONS_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "list_projects" for tool in tools):
            raise RuntimeError("Operations MCP server did not expose list_projects")
        result = await client.call_tool("list_projects", {"customer_name": customer_name})
        if result.is_error:
            raise RuntimeError(f"Operations MCP tool failed: {result}")
        return result.content[0].text


async def create_project(customer_name: str, name: str) -> str:
    """Create a customer project through the Operations MCP server."""
    async with _client(OPERATIONS_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "create_project" for tool in tools):
            raise RuntimeError("Operations MCP server did not expose create_project")
        result = await client.call_tool(
            "create_project", {"customer_name": customer_name, "name": name}
        )
        if result.is_error:
            raise RuntimeError(f"Operations MCP tool failed: {result}")
        return result.content[0].text


async def list_tasks(project_id: int) -> str:
    """List project tasks through the Operations MCP server."""
    async with _client(OPERATIONS_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "list_tasks" for tool in tools):
            raise RuntimeError("Operations MCP server did not expose list_tasks")
        result = await client.call_tool("list_tasks", {"project_id": project_id})
        if result.is_error:
            raise RuntimeError(f"Operations MCP tool failed: {result}")
        return result.content[0].text


async def create_task(project_id: int, title: str) -> str:
    """Create a project task through the Operations MCP server."""
    async with _client(OPERATIONS_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "create_task" for tool in tools):
            raise RuntimeError("Operations MCP server did not expose create_task")
        result = await client.call_tool(
            "create_task", {"project_id": project_id, "title": title}
        )
        if result.is_error:
            raise RuntimeError(f"Operations MCP tool failed: {result}")
        return result.content[0].text


async def send_email(to: str, subject: str, body: str) -> str:
    """Send an email through the configured Communication MCP backend."""
    async with _client(COMMUNICATION_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "send_email" for tool in tools):
            raise RuntimeError("Communication MCP server did not expose send_email")
        result = await client.call_tool(
            "send_email", {"to": to, "subject": subject, "body": body}
        )
        if result.is_error:
            raise RuntimeError(f"Communication MCP tool failed: {result}")
        return result.content[0].text


async def get_email(email_id: str | int) -> str:
    """Retrieve an email through the configured Communication MCP backend."""
    async with _client(COMMUNICATION_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "get_email" for tool in tools):
            raise RuntimeError("Communication MCP server did not expose get_email")
        result = await client.call_tool("get_email", {"email_id": str(email_id)})
        if result.is_error:
            raise RuntimeError(f"Communication MCP tool failed: {result}")
        return result.content[0].text


async def list_invoices(customer_name: str) -> str:
    """List persisted invoices and revision relationships through Invoice MCP."""
    async with _client(INVOICE_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "list_invoices" for tool in tools):
            raise RuntimeError("Invoice MCP server did not expose list_invoices")
        result = await client.call_tool("list_invoices", {"customer_name": customer_name})
        if result.is_error:
            raise RuntimeError(f"Invoice MCP tool failed: {result}")
        return result.content[0].text


async def get_invoice(invoice_id: int) -> str:
    """Retrieve one persisted invoice through Invoice MCP."""
    async with _client(INVOICE_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "get_invoice" for tool in tools):
            raise RuntimeError("Invoice MCP server did not expose get_invoice")
        result = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        if result.is_error:
            raise RuntimeError(f"Invoice MCP tool failed: {result}")
        return result.content[0].text


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
    """Create or update an invoice through Invoice MCP."""
    async with _client(INVOICE_SERVER_PATH) as client:
        tools = await client.list_tools()
        if not any(tool.name == "log_invoice" for tool in tools):
            raise RuntimeError("Invoice MCP server did not expose log_invoice")
        result = await client.call_tool(
            "log_invoice",
            {
                "customer_name": customer_name,
                "invoice_number": invoice_number,
                "amount": amount,
                "due_date": due_date,
                "issued_date": issued_date,
                "revision_number": revision_number,
                "status": status,
                "supersedes_invoice_number": supersedes_invoice_number,
            },
        )
        if result.is_error:
            raise RuntimeError(f"Invoice MCP tool failed: {result}")
        return result.content[0].text
