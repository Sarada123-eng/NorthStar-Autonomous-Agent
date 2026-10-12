import json
import re
from typing import Any, Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.agent.prompts import COMPLETION_REVIEW_PROMPT


ParseToolResult = Callable[[str], dict[str, Any]]
AsyncToolCall = Callable[..., Awaitable[str]]

_COMPLETION_GENERIC_TERMS = {
    "a", "an", "and", "assessment", "customer", "for", "initial",
    "mandatory", "onboarding", "review", "setup", "task", "the", "to",
    "verification", "work",
}


def json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return parsed


async def completion_documents(
    search_results: list[dict[str, Any]],
    legal_name: str,
    *,
    parse_tool_result: ParseToolResult,
    call_search_documents: AsyncToolCall,
    call_read_document: AsyncToolCall,
    reference_documents: tuple[str, ...],
    max_document_chars: int,
) -> dict[str, str]:
    """Read governing and reference documents in full for the review."""
    legal = " ".join(legal_name.casefold().split())
    candidates: list[str] = []
    for chunk in (search_results if isinstance(search_results, list) else []):
        source = chunk.get("source") if isinstance(chunk, dict) else None
        if (
            isinstance(source, str)
            and source.startswith("contracts/")
            and source not in candidates
        ):
            candidates.append(source)
    if not candidates:
        try:
            results = json.loads(await call_search_documents(f"{legal_name} contract"))
            for chunk in (results if isinstance(results, list) else []):
                source = chunk.get("source") if isinstance(chunk, dict) else None
                if (
                    isinstance(source, str)
                    and source.startswith("contracts/")
                    and source not in candidates
                ):
                    candidates.append(source)
        except (TypeError, json.JSONDecodeError):
            pass

    documents: dict[str, str] = {}
    for source in candidates[:4]:
        payload = parse_tool_result(await call_read_document(source))
        content = payload.get("content")
        if (
            payload.get("success") is True
            and isinstance(content, str)
            and legal in " ".join(content.casefold().split())
        ):
            documents[source] = content[:max_document_chars]
    if not documents:
        raise RuntimeError(f"governing contract for {legal_name} not found")
    for source in reference_documents:
        payload = parse_tool_result(await call_read_document(source))
        if payload.get("success") is True and isinstance(payload.get("content"), str):
            documents[source] = payload["content"][:max_document_chars]
    return documents


async def completion_actual_tasks(
    legal_name: str,
    project_ids: set[int],
    *,
    parse_tool_result: ParseToolResult,
    call_list_projects: AsyncToolCall,
    call_list_tasks: AsyncToolCall,
) -> list[dict[str, Any]]:
    """Read persisted tasks for the projects touched by this run."""
    if not project_ids:
        return []
    projects_result = parse_tool_result(await call_list_projects(legal_name))
    projects = projects_result.get("projects")
    if projects_result.get("success") is not True or not isinstance(projects, list):
        raise RuntimeError("could not list the customer's onboarding projects")
    rows: list[dict[str, Any]] = []
    for project in projects:
        if (
            not isinstance(project, dict)
            or not isinstance(project.get("id"), int)
            or project["id"] not in project_ids
        ):
            continue
        tasks_result = parse_tool_result(await call_list_tasks(project["id"]))
        tasks = tasks_result.get("tasks")
        if tasks_result.get("success") is not True or not isinstance(tasks, list):
            raise RuntimeError("could not list onboarding tasks")
        for task in tasks:
            if isinstance(task, dict) and isinstance(task.get("id"), int):
                rows.append(
                    {
                        "task_id": task["id"],
                        "project": project.get("name"),
                        "title": task.get("title"),
                        "status": task.get("status"),
                    }
                )
    return rows


def completion_terms(text: Any) -> set[str]:
    terms = set()
    for term in re.findall(r"[a-z0-9]+", str(text).casefold()):
        if term.endswith("s") and len(term) > 4:
            term = term[:-1]
        if term not in _COMPLETION_GENERIC_TERMS and len(term) > 2:
            terms.add(term)
    return terms


def task_coverage_evidence(item: str, task: dict[str, Any]) -> dict[str, Any]:
    requirement_terms = completion_terms(item)
    title = str(task.get("title", ""))
    title_terms = completion_terms(title)
    matched_terms = sorted(requirement_terms & title_terms)
    required_matches = min(2, len(requirement_terms))
    covered = bool(requirement_terms) and len(matched_terms) >= required_matches
    return {
        "covered": covered,
        "task_title": title,
        "matched_terms": matched_terms,
        "required_terms": sorted(requirement_terms),
    }


async def review_requirements(
    legal_name: str,
    documents: dict[str, str],
    tasks: list[dict[str, Any]],
    *,
    get_llm: Callable[[], Any],
    max_items: int,
) -> list[dict[str, Any]]:
    task_lines = (
        "\n".join(
            f"- id {task['task_id']}: {task['title']} [{task['status']}] "
            f"(project: {task['project']})"
            for task in tasks
        )
        or "(no tasks exist)"
    )
    document_text = "\n\n".join(
        f"### DOCUMENT: {source}\n{content}" for source, content in documents.items()
    )
    response = await get_llm().ainvoke(
        [
            SystemMessage(content=COMPLETION_REVIEW_PROMPT),
            HumanMessage(
                content=(
                    f"Customer legal entity: {legal_name}\n\n"
                    f"Existing onboarding tasks:\n{task_lines}\n\n{document_text}"
                )
            ),
        ]
    )
    items = json_object(str(response.content)).get("required_items")
    if not isinstance(items, list):
        raise ValueError("required_items missing from reviewer output")
    tasks_by_id = {
        task["task_id"]: task
        for task in tasks
        if isinstance(task.get("task_id"), int)
    }
    reviewed: list[dict[str, Any]] = []
    for item in items[:max_items]:
        if not isinstance(item, dict) or not str(item.get("item", "")).strip():
            continue
        claimed = item.get("satisfied_by_task_id")
        requirement = str(item["item"]).strip()
        task = tasks_by_id.get(claimed) if isinstance(claimed, int) else None
        evidence = (
            task_coverage_evidence(requirement, task)
            if task is not None
            else {
                "covered": False,
                "task_title": None,
                "matched_terms": [],
                "required_terms": sorted(completion_terms(requirement)),
            }
        )
        reviewed.append(
            {
                "item": requirement,
                "basis": str(item.get("basis", ""))[:300],
                "satisfied_by_task_id": (
                    claimed if task is not None and evidence["covered"] else None
                ),
                "coverage_evidence": evidence,
            }
        )
    return reviewed


async def verify_requirements(
    state: AgentState,
    *,
    search_results: list[dict[str, Any]],
    project_ids: set[int],
    parse_tool_result: ParseToolResult,
    call_search_documents: AsyncToolCall,
    call_read_document: AsyncToolCall,
    call_list_projects: AsyncToolCall,
    call_list_tasks: AsyncToolCall,
    get_llm: Callable[[], Any],
    reference_documents: tuple[str, ...],
    max_document_chars: int,
    max_items: int,
    max_repairs: int,
) -> dict:
    """Compare persisted onboarding tasks with contract/SOP requirements."""
    attempt = state.get("completion_check_count", 0) + 1
    legal_name = state["customer_resolution"]["customer"]["legal_name"]
    try:
        documents = await completion_documents(
            search_results,
            legal_name,
            parse_tool_result=parse_tool_result,
            call_search_documents=call_search_documents,
            call_read_document=call_read_document,
            reference_documents=reference_documents,
            max_document_chars=max_document_chars,
        )
        tasks = await completion_actual_tasks(
            legal_name,
            project_ids,
            parse_tool_result=parse_tool_result,
            call_list_projects=call_list_projects,
            call_list_tasks=call_list_tasks,
        )
        reviewed = await review_requirements(
            legal_name,
            documents,
            tasks,
            get_llm=get_llm,
            max_items=max_items,
        )
    except Exception as error:
        return {
            "onboarding_completion_result": {
                "status": "SKIPPED",
                "attempt": attempt,
                "reason": f"{type(error).__name__}: {error}"[:200],
            },
            "completion_check_count": attempt,
        }

    verification_evidence = [
        {"source": source, "excerpt": content}
        for source, content in documents.items()
        if isinstance(source, str) and isinstance(content, str) and content.strip()
    ]
    missing = [item["item"] for item in reviewed if item["satisfied_by_task_id"] is None]
    status = "SKIPPED" if not reviewed else ("FAIL" if missing else "PASS")
    update: dict[str, Any] = {
        "onboarding_completion_result": {
            "status": status,
            "attempt": attempt,
            "required_items": reviewed,
            "missing": missing,
        },
        "completion_check_count": attempt,
        "verification_evidence": verification_evidence,
    }
    if status != "FAIL":
        return update

    listed = "; ".join(missing)
    if attempt <= max_repairs:
        update["messages"] = [
            HumanMessage(
                content=(
                    "Independent completion check: the governing documents require "
                    f"the following onboarding items, but no matching task exists: {listed}. "
                    "The completion check has already independently inspected the "
                    "current persisted tasks for this run's onboarding project. Reuse "
                    "that evidence and create one internal task for each missing item; "
                    "do not repeat list_tasks for the same project solely to rediscover "
                    "these gaps. If an item needs a "
                    "decision or approval from another role (for example Security, "
                    "Finance or an operations lead), record it as a task whose title "
                    "begins with 'Escalate:' (use 'Review:' for reviews or assessments that "
                    "Northstar staff will carry out) and say in your final answer "
                    "that it is pending, not done. Do not claim anything is complete "
                    "that has not been done."
                )
            )
        ]
        return update

    update["execution_stop_reason"] = "onboarding_requirements_incomplete"
    update["messages"] = [
        HumanMessage(
            content=(
                f"Independent completion check still found required onboarding items "
                f"with no matching task after {max_repairs} repair "
                f"attempts: {listed}. Report the onboarding status accurately: say "
                "what was done, and list these as open items that need human action. "
                "Do not claim onboarding is complete."
            )
        )
    ]
    return update
