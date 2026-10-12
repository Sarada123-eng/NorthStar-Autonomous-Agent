import json
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import create_customer as insert_customer
from app.database import create_project as insert_project
from app.database import create_task as insert_task
from app.database import find_customer as query_customer
from app.database import find_customer_by_contact as query_customer_by_contact
from app.database import list_projects as query_projects
from app.database import list_tasks as query_tasks

mcp = FastMCP("Northstar Operations Server")


def _transient_failure_state_path() -> Path:
    configured_path = os.getenv("NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE")
    if configured_path:
        return Path(configured_path)

    specification = os.getenv("NORTHSTAR_TRANSIENT_FAILURES", "")
    digest = hashlib.sha256(specification.encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"northstar-transient-{digest}.json"


def _consume_transient_failure(tool_name: str) -> bool:
    specification = os.getenv("NORTHSTAR_TRANSIENT_FAILURES", "")
    configured_failures = {}
    for entry in specification.split(","):
        if not entry.strip():
            continue
        name, separator, count = entry.partition(":")
        if separator and count.isdigit():
            configured_failures[name.strip()] = int(count)

    remaining = configured_failures.get(tool_name, 0)
    if remaining <= 0:
        return False

    state_path = _transient_failure_state_path()
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text())
    consumed = int(state.get(tool_name, 0))
    if consumed >= remaining:
        return False

    state[tool_name] = consumed + 1
    state_path.write_text(json.dumps(state))
    return True


@mcp.tool
async def find_customer(name: str) -> str:
    """Find a customer record in the Northstar Operations database."""
    return json.dumps(await query_customer(name))


@mcp.tool
async def find_customer_by_contact(recipient: str) -> str:
    """Find the customer record containing an exact communication contact."""
    return json.dumps(await query_customer_by_contact(recipient))


@mcp.tool
async def create_customer(
    name: str,
    billing_contact: str,
    technical_contact: str,
) -> str:
    """Create a new customer with onboarding status."""
    if _consume_transient_failure("create_customer"):
        return json.dumps(
            {
                "success": False,
                "error_type": "transient",
                "retryable": True,
                "message": "Temporary operations-system failure.",
            }
        )

    result = await insert_customer(name, billing_contact, technical_contact)
    return json.dumps(result)


@mcp.tool
async def list_projects(customer_name: str) -> str:
    """List onboarding projects for a customer."""
    return json.dumps(await query_projects(customer_name))


@mcp.tool
async def create_project(customer_name: str, name: str) -> str:
    """Create an internal onboarding project for an existing customer."""
    if _consume_transient_failure("create_project"):
        return json.dumps(
            {
                "success": False,
                "error_type": "transient",
                "retryable": True,
                "message": "Temporary operations-system failure.",
            }
        )
    return json.dumps(await insert_project(customer_name, name))


@mcp.tool
async def list_tasks(project_id: int) -> str:
    """List tasks in an onboarding project."""
    return json.dumps(await query_tasks(project_id))


@mcp.tool
async def create_task(project_id: int, title: str) -> str:
    """Create an internal onboarding task in a project."""
    if _consume_transient_failure("create_task"):
        return json.dumps(
            {
                "success": False,
                "error_type": "transient",
                "retryable": True,
                "message": "Temporary operations-system failure.",
            }
        )
    return json.dumps(await insert_task(project_id, title))


if __name__ == "__main__":
    mcp.run()
