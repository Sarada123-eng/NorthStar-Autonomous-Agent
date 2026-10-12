from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, tool


ToolDependencyResolver = Callable[[str], Callable[..., Awaitable[str]]]


def build_tools(resolve_dependency: ToolDependencyResolver) -> dict[str, BaseTool]:
    """Build the LangChain tools around runtime-resolved MCP callables."""

    @tool
    async def search_documents(query: str) -> str:
        """Search Northstar policies, SOPs, contracts, invoices, and internal documents.

        Use this for security requirements and all questions about documented
        company knowledge. Do not use it for current customer records or
        operational database state.
        """
        return await resolve_dependency("search_documents")(query)

    @tool
    async def find_customer(name: str) -> str:
        """Find a current customer record and status in the Operations database.

        Use this only when the user asks whether a customer exists or asks about
        current customer status. Never use it for security requirements, policies,
        contracts, invoices, or other internal documents. After a duplicate
        creation, this confirms only that a short-name record exists; it does not
        prove that the requested legal entity is the same. Use
        search_documents to compare legal name, registration or tax identifier,
        and domain before reusing that record.
        """
        return await resolve_dependency("find_customer")(name)

    @tool
    async def create_customer(
        name: str,
        billing_contact: str,
        technical_contact: str,
    ) -> str:
        """Create a customer in the Operations database.

        Use only when the user explicitly requests creation and provides the
        customer's name, billing contact, and technical contact.
        """
        return await resolve_dependency("create_customer")(
            name, billing_contact, technical_contact
        )

    @tool
    async def list_projects(customer_name: str) -> str:
        """List existing onboarding projects for a customer before creating one."""
        return await resolve_dependency("list_projects")(customer_name)

    @tool
    async def create_project(customer_name: str, name: str) -> str:
        """Create an internal onboarding project for an existing customer."""
        return await resolve_dependency("create_project")(customer_name, name)

    @tool
    async def list_tasks(project_id: int) -> str:
        """List existing tasks in an onboarding project before creating tasks."""
        return await resolve_dependency("list_tasks")(project_id)

    @tool
    async def create_task(project_id: int, title: str) -> str:
        """Create one internal onboarding task in an existing project."""
        return await resolve_dependency("create_task")(project_id, title)

    @tool
    async def list_invoices(customer_name: str) -> str:
        """List a customer's persisted invoices and their revision relationships."""
        return await resolve_dependency("list_invoices")(customer_name)

    @tool
    async def get_invoice(invoice_id: int) -> str:
        """Retrieve one persisted invoice by invoice_id for inspection or verification."""
        return await resolve_dependency("get_invoice")(invoice_id)

    @tool
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
        """Create or update an invoice with amount, dates, status, and revision link."""
        return await resolve_dependency("log_invoice")(
            customer_name,
            invoice_number,
            amount,
            due_date,
            issued_date,
            revision_number,
            status,
            supersedes_invoice_number,
        )

    @tool
    async def send_email(to: str, subject: str, body: str) -> str:
        """Send or record an email through the configured communication backend."""
        return await resolve_dependency("send_email")(to, subject, body)

    @tool
    async def get_email(email_id: int) -> str:
        """Retrieve the resulting email record from the configured backend."""
        return await resolve_dependency("get_email")(email_id)

    @tool
    def request_clarification(question: str) -> str:
        """Ask the user for missing or ambiguous information before continuing."""
        return question

    return {
        "search_documents": search_documents,
        "find_customer": find_customer,
        "create_customer": create_customer,
        "list_projects": list_projects,
        "create_project": create_project,
        "list_tasks": list_tasks,
        "create_task": create_task,
        "list_invoices": list_invoices,
        "get_invoice": get_invoice,
        "log_invoice": log_invoice,
        "send_email": send_email,
        "get_email": get_email,
        "request_clarification": request_clarification,
    }
