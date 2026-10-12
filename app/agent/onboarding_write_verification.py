from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.state import AgentState


ParseToolResult = Callable[[Any], dict[str, Any]]
ListProjects = Callable[[str], Awaitable[str]]
ListTasks = Callable[[int], Awaitable[str]]
ResolutionMatchesReference = Callable[
    [AgentState, dict[str, Any] | None, str | None], bool
]


def last_onboarding_writes(
    state: AgentState,
    parse_tool_result: ParseToolResult,
    onboarding_write_tools: set[str],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair successful project/task calls with their direct execution results."""
    last_calls: list[dict[str, Any]] = []
    for message in reversed(state["messages"]):
        if isinstance(message, AIMessage):
            last_calls = message.tool_calls
            break
    results = (state.get("last_tool_result") or {}).get("tools", [])
    writes = []
    for tool_call, result in zip(last_calls, results):
        if (
            tool_call["name"] in onboarding_write_tools
            and result.get("tool") == tool_call["name"]
            and parse_tool_result(result.get("content", "")).get("success") is True
        ):
            writes.append((tool_call, result))
    return writes


def _resolved_customer(
    state: AgentState,
    customer_name: str | None,
    resolution_matches_reference: ResolutionMatchesReference,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return run-bound customer evidence and an MCP lookup reference."""
    resolution = state.get("customer_resolution")
    if not resolution_matches_reference(state, resolution, customer_name):
        return None, None
    customer = resolution.get("customer") if resolution else None
    if not isinstance(customer, dict) or not isinstance(customer.get("id"), int):
        return None, None
    reference = customer.get("legal_name") or customer.get("name")
    return (customer, reference) if isinstance(reference, str) and reference else (None, None)


def _observed_project(
    state: AgentState,
    project_id: int,
    customer_id: int,
    parse_tool_result: ParseToolResult,
) -> bool:
    """Require a task's project ID to come from prior persisted tool evidence."""
    for message in state["messages"]:
        if not isinstance(message, ToolMessage):
            continue
        result = parse_tool_result(message.content)
        candidates = []
        if isinstance(result.get("project"), dict):
            candidates.append(result["project"])
        if isinstance(result.get("projects"), list):
            candidates.extend(result["projects"])
        if any(
            isinstance(project, dict)
            and project.get("id") == project_id
            and project.get("customer_id") == customer_id
            for project in candidates
        ):
            return True
    return False


async def verify_created_project(
    state: AgentState,
    action: dict[str, Any],
    execution: dict[str, Any],
    *,
    parse_tool_result: ParseToolResult,
    call_list_projects: ListProjects,
    resolution_matches_reference: ResolutionMatchesReference,
) -> dict[str, Any]:
    created = execution.get("project")
    customer, customer_reference = _resolved_customer(
        state,
        action["args"].get("customer_name"),
        resolution_matches_reference,
    )
    if not isinstance(created, dict) or customer is None or customer_reference is None:
        return {
            "status": "FAIL",
            "kind": "project",
            "reason": "missing_execution_evidence",
        }
    project_id = created.get("id")
    created_customer_id = created.get("customer_id")
    if not isinstance(project_id, int) or not isinstance(created_customer_id, int):
        return {
            "status": "FAIL",
            "kind": "project",
            "reason": "missing_project_identifier",
        }
    try:
        read_result = parse_tool_result(await call_list_projects(customer_reference))
    except Exception:
        read_result = {}
    projects = read_result.get("projects")
    actual = next(
        (
            project
            for project in projects
            if isinstance(project, dict) and project.get("id") == project_id
        ),
        None,
    ) if read_result.get("success") is True and isinstance(projects, list) else None
    if not isinstance(actual, dict):
        return {
            "status": "FAIL",
            "kind": "project",
            "reason": "project_not_found",
            "project_id": project_id,
        }
    expected = {
        "id": project_id,
        "customer_id": customer["id"],
        "name": action["args"].get("name"),
        "status": created.get("status"),
    }
    mismatches = {
        field: {"expected": value, "actual": actual.get(field)}
        for field, value in expected.items()
        if actual.get(field) != value
    }
    if created_customer_id != customer["id"]:
        mismatches["creation_customer_id"] = {
            "expected": customer["id"],
            "actual": created_customer_id,
        }
    if mismatches:
        return {
            "status": "FAIL",
            "kind": "project",
            "reason": next(iter(mismatches)),
            "mismatches": mismatches,
        }
    return {"status": "PASS", "kind": "project", "project_id": project_id}


async def verify_created_task(
    state: AgentState,
    action: dict[str, Any],
    execution: dict[str, Any],
    *,
    parse_tool_result: ParseToolResult,
    call_list_projects: ListProjects,
    call_list_tasks: ListTasks,
    resolution_matches_reference: ResolutionMatchesReference,
) -> dict[str, Any]:
    created = execution.get("task")
    customer, customer_reference = _resolved_customer(
        state, None, resolution_matches_reference
    )
    if not isinstance(created, dict) or customer is None or customer_reference is None:
        return {
            "status": "FAIL",
            "kind": "task",
            "reason": "missing_execution_evidence",
        }
    task_id = created.get("id")
    project_id = action["args"].get("project_id")
    if not isinstance(task_id, int) or not isinstance(project_id, int):
        return {
            "status": "FAIL",
            "kind": "task",
            "reason": "missing_task_identifier",
        }
    if created.get("project_id") != project_id:
        return {
            "status": "FAIL",
            "kind": "task",
            "reason": "creation_project_id",
            "mismatches": {
                "project_id": {
                    "expected": project_id,
                    "actual": created.get("project_id"),
                }
            },
        }
    if not _observed_project(state, project_id, customer["id"], parse_tool_result):
        return {
            "status": "FAIL",
            "kind": "task",
            "reason": "unobserved_project_id",
            "project_id": project_id,
        }
    try:
        project_result = parse_tool_result(await call_list_projects(customer_reference))
    except Exception:
        project_result = {}
    projects = project_result.get("projects")
    project = next(
        (
            item
            for item in projects
            if isinstance(item, dict)
            and item.get("id") == project_id
            and item.get("customer_id") == customer["id"]
        ),
        None,
    ) if project_result.get("success") is True and isinstance(projects, list) else None
    if not isinstance(project, dict):
        return {
            "status": "FAIL",
            "kind": "task",
            "reason": "project_not_found_or_wrong_customer",
            "project_id": project_id,
        }
    try:
        task_result = parse_tool_result(await call_list_tasks(project_id))
    except Exception:
        task_result = {}
    tasks = task_result.get("tasks")
    actual = next(
        (
            task
            for task in tasks
            if isinstance(task, dict) and task.get("id") == task_id
        ),
        None,
    ) if task_result.get("success") is True and isinstance(tasks, list) else None
    if not isinstance(actual, dict):
        return {
            "status": "FAIL",
            "kind": "task",
            "reason": "task_not_found",
            "task_id": task_id,
        }
    expected = {
        "id": task_id,
        "project_id": project_id,
        "title": action["args"].get("title"),
        "status": created.get("status"),
    }
    mismatches = {
        field: {"expected": value, "actual": actual.get(field)}
        for field, value in expected.items()
        if actual.get(field) != value
    }
    if mismatches:
        return {
            "status": "FAIL",
            "kind": "task",
            "reason": next(iter(mismatches)),
            "mismatches": mismatches,
        }
    return {"status": "PASS", "kind": "task", "task_id": task_id, "project_id": project_id}


async def verify_onboarding(
    state: AgentState,
    *,
    parse_tool_result: ParseToolResult,
    call_list_projects: ListProjects,
    call_list_tasks: ListTasks,
    resolution_matches_reference: ResolutionMatchesReference,
    onboarding_write_tools: set[str],
) -> dict:
    """Read back successful onboarding writes without proposing corrective writes."""
    writes = last_onboarding_writes(state, parse_tool_result, onboarding_write_tools)
    if not writes:
        evidence = {
            "status": "FAIL",
            "reason": "missing_execution_evidence",
        }
    else:
        evidence_items = []
        for action, result in writes:
            execution = parse_tool_result(result["content"])
            if action["name"] == "create_project":
                item = await verify_created_project(
                    state,
                    action,
                    execution,
                    parse_tool_result=parse_tool_result,
                    call_list_projects=call_list_projects,
                    resolution_matches_reference=resolution_matches_reference,
                )
            else:
                item = await verify_created_task(
                    state,
                    action,
                    execution,
                    parse_tool_result=parse_tool_result,
                    call_list_projects=call_list_projects,
                    call_list_tasks=call_list_tasks,
                    resolution_matches_reference=resolution_matches_reference,
                )
            evidence_items.append(item)
            evidence = item
            if item["status"] == "FAIL":
                break
        else:
            previous = state.get("onboarding_verification_result") or {}
            previous_writes = (
                previous.get("writes", []) if previous.get("status") == "PASS" else []
            )
            evidence = {"status": "PASS", "writes": [*previous_writes, *evidence_items]}
    return {
        "onboarding_verification_result": evidence,
        "execution_stop_reason": (
            "onboarding_verification_failed" if evidence["status"] == "FAIL" else None
        ),
    }
