import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.mcp_client import (
    create_customer as call_create_customer,
    create_project as call_create_project,
    create_task as call_create_task,
    find_customer as call_find_customer,
    find_customer_by_contact as call_find_customer_by_contact,
    get_email as call_get_email,
    get_invoice as call_get_invoice,
    list_projects as call_list_projects,
    list_invoices as call_list_invoices,
    list_tasks as call_list_tasks,
    read_document as call_read_document,
    search_documents as call_search_documents,
    send_email as call_send_email,
    log_invoice as call_log_invoice,
)
from app.agent.state import AgentState, RunStatus
from app.agent import invoice_reconciliation
from app.agent import onboarding_verification
from app.agent import communication_verification
from app.agent import customer_resolution
from app.agent import execution_support
from app.agent import onboarding_write_verification
from app.agent.prompts import COMPLETION_REVIEW_PROMPT, SYSTEM_PROMPT
from app.agent.tools import build_tools
from app.policy.communication import (
    ALLOW,
    APPROVAL_REQUIRED,
    DENY,
    evaluate_email,
)

load_dotenv()

MAX_TOOL_STEPS = 20
MAX_RETRIES_PER_ACTION = 3
MAX_VERIFICATION_REPAIRS = 2
MAX_COMPLETION_REPAIRS = 2
MAX_COMPLETION_ITEMS = 15
MAX_COMPLETION_DOC_CHARS = 60000
# Reference documents the independent completion review always reads in full,
# in addition to the customer's governing contract.
COMPLETION_REFERENCE_DOCS = (
    "company/onboarding_sop.md",
    "company/security_policy.md",
)
SMOKE_READ_ONLY_WRITE_TOOLS = {
    "create_customer",
    "create_project",
    "create_task",
    "log_invoice",
    "send_email",
}

def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
    )


def _resolve_tool_dependency(name: str):
    return {
        "search_documents": call_search_documents,
        "find_customer": call_find_customer,
        "create_customer": call_create_customer,
        "list_projects": call_list_projects,
        "create_project": call_create_project,
        "list_tasks": call_list_tasks,
        "create_task": call_create_task,
        "list_invoices": call_list_invoices,
        "get_invoice": call_get_invoice,
        "log_invoice": call_log_invoice,
        "send_email": call_send_email,
        "get_email": call_get_email,
    }[name]


_TOOLS = build_tools(_resolve_tool_dependency)
search_documents = _TOOLS["search_documents"]
find_customer = _TOOLS["find_customer"]
create_customer = _TOOLS["create_customer"]
list_projects = _TOOLS["list_projects"]
create_project = _TOOLS["create_project"]
list_tasks = _TOOLS["list_tasks"]
create_task = _TOOLS["create_task"]
list_invoices = _TOOLS["list_invoices"]
get_invoice = _TOOLS["get_invoice"]
log_invoice = _TOOLS["log_invoice"]
send_email = _TOOLS["send_email"]
get_email = _TOOLS["get_email"]
request_clarification = _TOOLS["request_clarification"]


async def decide(state: AgentState) -> dict:
    """Let the model choose the next tool from the current observations."""
    llm = get_llm().bind_tools(
        [
            search_documents,
            find_customer,
            create_customer,
            list_projects,
            create_project,
            list_tasks,
            create_task,
            list_invoices,
            get_invoice,
            log_invoice,
            send_email,
            get_email,
            request_clarification,
        ]
    )
    messages = state["messages"]
    if not messages:
        messages = [HumanMessage(content=state["goal"])]
    prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)]
    if state.get("onboarding_verification_result"):
        prompt_messages.append(_onboarding_verification_instruction(state))
    if state.get("invoice_reconciliation_result"):
        prompt_messages.append(
            SystemMessage(
                content=(
                    "Report the deterministic invoice reconciliation accurately. "
                    "VERIFIED means the selected active persisted invoice matched "
                    "the available internal invoice document on the checked fields; "
                    "it does not prove commercial or legal correctness. Do not claim "
                    "verification when the result is FAILED or INSUFFICIENT_EVIDENCE."
                )
            )
        )
    response = await llm.ainvoke([*prompt_messages, *messages])
    return {
        "messages": ([HumanMessage(content=state["goal"])] if not state["messages"] else [])
        + [response]
    }


def route_after_decision(state: AgentState) -> str:
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        if any(
            tool_call["name"] == "request_clarification"
            for tool_call in last_message.tool_calls
        ):
            return "clarify"
        if _requires_onboarding_knowledge(state):
            return "retrieve_onboarding_knowledge"
        if any(
            tool_call["name"] in CUSTOMER_SCOPED_ACTION_TOOLS
            for tool_call in last_message.tool_calls
        ):
            return "customer_resolution_gate"
        return "execute_tool"
    if _should_run_completion_check(state):
        return "verify_requirements"
    if _should_run_invoice_reconciliation(state):
        return "reconcile_invoice"
    return END


def _requires_onboarding_knowledge(state: AgentState) -> bool:
    """Require customer-specific procedure and contract evidence before a write."""
    if "onboard" not in state["goal"].casefold():
        return False
    # Identity resolution remains the first safety boundary.  A knowledge
    # lookup must not allow a proposed write to skip clarification.
    if (state.get("customer_resolution") or {}).get("status") != "resolved":
        return False
    last_message = state["messages"][-1]
    is_onboarding_write = isinstance(last_message, AIMessage) and any(
        call["name"] in ONBOARDING_WRITE_TOOLS for call in last_message.tool_calls
    )
    return is_onboarding_write and bool(_onboarding_knowledge_gaps(state))


def _search_results(state: AgentState) -> list[dict[str, Any]]:
    """Return only structured chunks actually returned by Knowledge MCP."""
    search_call_ids = {
        call["id"]
        for message in state["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] == "search_documents"
    }
    results: list[dict[str, Any]] = []
    for message in state["messages"]:
        if not isinstance(message, ToolMessage) or message.tool_call_id not in search_call_ids:
            continue
        try:
            payload = json.loads(message.content)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        results.extend(item for item in payload if isinstance(item, dict))
    return results


def _onboarding_knowledge_gaps(state: AgentState) -> list[str]:
    """Identify required evidence from retrieved chunks, not search success alone."""
    resolution = state.get("customer_resolution") or {}
    customer = resolution.get("customer")
    legal_name = customer.get("legal_name") if isinstance(customer, dict) else None
    if not isinstance(legal_name, str) or not legal_name.strip():
        return ["resolved customer legal name"]

    chunks = _search_results(state)
    has_sop = any(chunk.get("source") == "company/onboarding_sop.md" for chunk in chunks)
    legal_name_normalized = " ".join(legal_name.casefold().split())
    has_contract = any(
        isinstance(chunk.get("source"), str)
        and str(chunk["source"]).startswith("contracts/")
        and isinstance(chunk.get("content"), str)
        and legal_name_normalized in " ".join(chunk["content"].casefold().split())
        for chunk in chunks
    )
    gaps = []
    if not has_sop:
        gaps.append("onboarding SOP")
    if not has_contract:
        gaps.append(f"governing contract for {legal_name}")
    return gaps


async def retrieve_onboarding_knowledge(state: AgentState) -> dict:
    """Obtain procedure evidence required before an onboarding write."""
    proposed_calls = state["messages"][-1].tool_calls
    deferred = json.dumps(
        {
            "success": False,
            "error_type": "onboarding_knowledge_required",
            "retryable": False,
            "message": (
                "Onboarding writes require consultation of the documented "
                "onboarding procedure and applicable policies first."
            ),
        }
    )
    resolution = state.get("customer_resolution") or {}
    customer = resolution.get("customer")
    legal_name = customer.get("legal_name") if isinstance(customer, dict) else None
    missing = _onboarding_knowledge_gaps(state)
    search_call = {
        "name": "search_documents",
        "args": {
            "query": (
                f"{legal_name} governing contract onboarding SOP security policy"
                if isinstance(legal_name, str) and legal_name
                else f"{state['goal']} onboarding procedure policies"
            )
        },
        "id": "required-onboarding-knowledge",
        "type": "tool_call",
    }
    try:
        result = await call_search_documents(search_call["args"]["query"])
    except Exception as error:
        result = _structured_tool_exception(error)
    # Evaluate the returned chunks together with any prior Knowledge MCP
    # observations. A generic successful search is not sufficient evidence.
    evidence_messages = [
        AIMessage(content="", tool_calls=[search_call]),
        ToolMessage(content=result, tool_call_id=search_call["id"]),
    ]
    evidence_state = {**state, "messages": [*state["messages"], *evidence_messages]}
    remaining = _onboarding_knowledge_gaps(evidence_state)
    unavailable = bool(remaining)
    return {
        "messages": [
            ToolMessage(content=deferred, tool_call_id=call["id"])
            for call in proposed_calls
        ]
        + evidence_messages,
        "last_tool_result": {
            "tools": [{"tool": "search_documents", "content": result}]
        },
        "last_tool_succeeded": not unavailable,
        "execution_stop_reason": (
            "onboarding_knowledge_evidence_missing" if unavailable else None
        ),
    }


async def customer_resolution_gate(state: AgentState) -> dict:
    """Require identity resolution before customer-scoped execution."""
    resolution = state.get("customer_resolution")
    # The user goal, not an LLM-proposed tool argument, is the initial source
    # of customer identity.  A tool argument is checked only against identity
    # evidence already resolved for this run.
    reference = _explicit_customer_reference(state["goal"])
    action_reference = _customer_reference_for_current_actions(state)
    if _resolution_matches_reference(
        state, resolution, reference
    ) and _resolution_matches_reference(state, resolution, action_reference):
        return {}

    # Resolution evidence is run-bound.  Do not reuse an unbound, stale, or
    # differently named customer when this run is about to perform an action.
    if not _resolution_matches_reference(state, resolution, reference):
        resolution = None

    if not resolution or resolution.get("status") == "unresolved":
        if reference:
            lookup_result = parse_tool_result(await call_find_customer(reference))
            resolution = _bind_customer_resolution(
                state,
                _customer_resolution_from_result("find_customer", lookup_result),
                reference,
                lookup_result,
            )
            if _resolution_matches_reference(state, resolution, reference):
                return {"customer_resolution": resolution}

    question = _resolution_question(state)
    blocked_result = json.dumps(
        {
            "success": False,
            "error_type": "customer_resolution_required",
            "retryable": False,
            "resolution_status": (
                resolution.get("status") if resolution else "unresolved"
            ),
            "message": question,
        }
    )
    clarification_call = {
        "name": "request_clarification",
        "args": {"question": question},
        "id": "customer-resolution-gate",
        "type": "tool_call",
    }
    # The normal path enters this gate from the planner's AIMessage.  An
    # ambiguous find_customer result enters immediately after its ToolMessage,
    # which already has a result and therefore needs no duplicate one.
    current_calls = (
        state["messages"][-1].tool_calls
        if isinstance(state["messages"][-1], AIMessage)
        else []
    )
    return {
        "messages": [
            ToolMessage(
                content=blocked_result,
                tool_call_id=tool_call["id"],
            )
            for tool_call in current_calls
        ]
        + [AIMessage(content="", tool_calls=[clarification_call])],
        "status": RunStatus.WAITING_FOR_CLARIFICATION.value,
        "pending_human_input": question,
        "pending_human_input_type": "clarification",
        "customer_resolution": resolution,
    }


async def clarify(state: AgentState) -> dict:
    """Pause this checkpointed run until the user answers the model's question."""
    tool_call = next(
        call
        for message in reversed(state["messages"])
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] == "request_clarification"
    )
    question = tool_call["args"]["question"]
    response = interrupt(
        {
            "type": "clarification",
            "question": question,
            "goal": state["goal"],
        }
    )
    resolution_update = state.get("customer_resolution")
    # A gate-generated clarification asks the user to identify the customer.
    # Resolve that answer before planning again, so the next decision sees the
    # verified context instead of needing to rediscover the same legal name.
    if tool_call["id"] == "customer-resolution-gate" or (
        resolution_update is not None
        and resolution_update.get("status") in ("ambiguous", "not_found")
    ):
        reference = str(response).strip()
        lookup_result = parse_tool_result(
            await call_find_customer(reference)
        )
        resolution_update = _bind_customer_resolution(
            state,
            _customer_resolution_from_result("find_customer", lookup_result),
            reference,
            lookup_result,
        )
    return {
        "status": "running",
        "pending_human_input": None,
        "pending_human_input_type": None,
        "human_response": str(response),
        "customer_resolution": resolution_update,
        "last_failed_non_retryable_action": None,
        "execution_stop_reason": None,
        "messages": [
            ToolMessage(
                content=f"Clarification requested: {question}",
                tool_call_id=tool_call["id"],
            ),
            HumanMessage(content=f"User clarification: {response}"),
        ],
    }


def parse_tool_result(result: str) -> dict[str, Any]:
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _structured_tool_exception(error: Exception) -> str:
    """Convert an expected MCP/database failure into a safe observation."""
    text = str(error).casefold()
    retryable = any(
        marker in text
        for marker in (
            "temporarily unavailable",
            "connection refused",
            "connection reset",
            "timeout",
            "timed out",
            "network",
        )
    )
    error_type = "transient_tool_error" if retryable else "tool_error"
    message = (
        "The operations tool is temporarily unavailable."
        if retryable
        else "The requested operations tool failed without completing the action."
    )
    if "foreign key" in text or "does not exist" in text and "project" in text:
        error_type = "invalid_project_id"
        retryable = False
        message = (
            "The supplied project_id does not identify an existing project. "
            "Use list_projects to obtain the valid project_id."
        )
    return json.dumps(
        {
            "success": False,
            "error_type": error_type,
            "error": {"type": error_type, "message": message},
            "retryable": retryable,
            "message": message,
        }
    )


CUSTOMER_SCOPED_ACTION_TOOLS = {
    "list_projects",
    "list_tasks",
    "create_project",
    "create_task",
    "list_invoices",
    "log_invoice",
}
CUSTOMER_SCOPED_WRITE_TOOLS = {"create_project", "create_task", "log_invoice"}
ONBOARDING_WRITE_TOOLS = {"create_project", "create_task"}


def _has_task_inspection(state: AgentState, project_id: int) -> bool:
    """Require an actual successful task-list result for this run's project."""
    list_task_calls = {
        call["id"]: call["args"].get("project_id")
        for message in state["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] == "list_tasks"
    }
    return any(
        isinstance(message, ToolMessage)
        and list_task_calls.get(message.tool_call_id) == project_id
        and parse_tool_result(message.content).get("success") is True
        for message in state["messages"]
    )


def _explicitly_requests_customer_creation(goal: str) -> bool:
    normalized = goal.casefold()
    named_customer_creation = any(
        phrase in normalized
        for phrase in (
            "create a customer",
            "create customer",
            "new customer",
            "register a customer",
            "register customer",
            "add a customer",
            "add customer",
        )
    )
    contact_details_for_creation = (
        any(
            phrase in normalized
            for phrase in (
                "billing contact",
                "technical contact",
                "supplied contacts",
            )
        )
        and any(phrase in normalized for phrase in ("create ", "new ", "register "))
    )
    return named_customer_creation or contact_details_for_creation


def _customer_resolution_from_result(
    tool_name: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    return customer_resolution.resolution_from_result(tool_name, result)


def _normalize_customer_reference(value: str) -> str:
    return customer_resolution.normalize_reference(value)


def _bind_customer_resolution(
    state: AgentState,
    resolution: dict[str, Any] | None,
    requested_reference: str,
    lookup_result: dict[str, Any],
) -> dict[str, Any] | None:
    return customer_resolution.bind_resolution(
        state, resolution, requested_reference, lookup_result
    )


def _customer_reference_for_current_actions(state: AgentState) -> str | None:
    return customer_resolution.customer_reference_for_current_actions(
        state, CUSTOMER_SCOPED_ACTION_TOOLS
    )


def _resolution_matches_reference(
    state: AgentState,
    resolution: dict[str, Any] | None,
    requested_reference: str | None,
) -> bool:
    return customer_resolution.resolution_matches_reference(
        state, resolution, requested_reference
    )


def _cached_customer_lookup(
    state: AgentState, customer_name: str
) -> str | None:
    return customer_resolution.cached_lookup(
        state, customer_name
    )


def _explicit_customer_reference(goal: str) -> str | None:
    return customer_resolution.explicit_customer_reference(goal)


def _resolution_question(state: AgentState) -> str:
    return customer_resolution.resolution_question(state, parse_tool_result)


def _blocked_customer_write(
    state: AgentState, tool_call: dict[str, Any]
) -> tuple[str, AIMessage]:
    return customer_resolution.blocked_customer_write(
        state, tool_call, parse_tool_result
    )


def latest_customer_context(state: AgentState) -> dict[str, Any] | None:
    return customer_resolution.latest_customer_context(state, parse_tool_result)


async def customer_context_for_recipient(
    state: AgentState, recipient: str
) -> dict[str, Any] | None:
    return await customer_resolution.customer_context_for_recipient(
        state,
        recipient,
        parse_tool_result=parse_tool_result,
        call_find_customer_by_contact=call_find_customer_by_contact,
    )


def _canonical_customer_args(
    state: AgentState, tool_call: dict[str, Any]
) -> dict[str, Any]:
    return customer_resolution.canonical_customer_args(
        state,
        tool_call,
        scoped_customer_tools={
            "list_projects",
            "create_project",
            "list_invoices",
            "log_invoice",
        },
        resolution_matches=_resolution_matches_reference,
    )


def _run_project_ids(state: AgentState, customer_id: Any) -> set[int]:
    """Projects this run created or worked on for the customer, not historical ones."""
    if not isinstance(customer_id, int):
        return set()
    candidates: set[int] = set()
    for message in state["messages"]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                project_id = call.get("args", {}).get("project_id")
                if call["name"] in {"list_tasks", "create_task"} and isinstance(
                    project_id, int
                ):
                    candidates.add(project_id)
        elif isinstance(message, ToolMessage):
            project = parse_tool_result(message.content).get("project")
            if isinstance(project, dict) and isinstance(project.get("id"), int):
                candidates.add(project["id"])
    return {
        project_id
        for project_id in candidates
        if _observed_project_for_customer(state, project_id, customer_id)
    }


def _action_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    return execution_support.action_signature(tool_name, arguments)


def _unexecuted_tool_messages(
    state: AgentState, answered: list[ToolMessage]
) -> list[ToolMessage]:
    return execution_support.unexecuted_tool_messages(state, answered)


async def execute_tool(state: AgentState) -> dict:
    """Execute requested tools through their MCP clients."""
    tool_functions = {
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
    tool_messages = []
    tool_results = []
    tool_step_count = state["tool_step_count"]
    last_action_signature = state["last_action_signature"]
    consecutive_action_count = state["consecutive_action_count"]
    last_failed_non_retryable_action = state["last_failed_non_retryable_action"]
    retry_counts = dict(state["retry_counts"])
    execution_stop_reason = None
    customer_resolution = state.get("customer_resolution")

    for tool_call in state["messages"][-1].tool_calls:
        selected_tool = tool_functions.get(tool_call["name"])
        if selected_tool is None:
            raise RuntimeError(f"Unsupported tool requested: {tool_call['name']}")

        tool_call = _canonical_customer_args(state, tool_call)

        if (
            os.getenv("NORTHSTAR_SMOKE_TEST_READ_ONLY") == "1"
            and tool_call["name"] in SMOKE_READ_ONLY_WRITE_TOOLS
        ):
            result = json.dumps(
                {
                    "success": False,
                    "error_type": "smoke_read_only",
                    "retryable": False,
                    "message": "Write-capable tools are blocked for this smoke read-only run.",
                }
            )
            tool_messages.append(
                ToolMessage(content=result, tool_call_id=tool_call["id"])
            )
            tool_results.append({"tool": tool_call["name"], "content": result})
            execution_stop_reason = "smoke_read_only"
            break

        action_signature = _action_signature(
            tool_call["name"], tool_call["args"]
        )
        if action_signature == last_action_signature:
            consecutive_action_count += 1
        else:
            consecutive_action_count = 1

        previous_result = {}
        if state["last_tool_result"] and state["last_tool_result"]["tools"]:
            previous_result = parse_tool_result(
                state["last_tool_result"]["tools"][-1]["content"]
            )
        same_retryable_action = (
            action_signature == last_action_signature
            and previous_result.get("retryable") is True
            and previous_result.get("success") is False
        )

        if action_signature == last_failed_non_retryable_action:
            blocked_result = {
                "success": False,
                "error_type": "execution_guard",
                "message": (
                    "Execution stopped because the same action was requested "
                    "repeatedly after it failed."
                ),
            }
            result = json.dumps(blocked_result)
            tool_messages.append(
                ToolMessage(content=result, tool_call_id=tool_call["id"])
            )
            tool_results.append({"tool": tool_call["name"], "content": result})
            execution_stop_reason = "repeated_action"
            break

        if tool_call["name"] == "create_task":
            project_id = tool_call["args"].get("project_id")
            if not isinstance(project_id, int) or not _has_task_inspection(
                state, project_id
            ):
                result = json.dumps(
                    {
                        "success": False,
                        "error_type": "task_inspection_required",
                        "retryable": False,
                        "message": (
                            "Before creating an onboarding task, use list_tasks "
                            f"to inspect the current tasks for project ID {project_id}."
                        ),
                    }
                )
                tool_messages.append(
                    ToolMessage(content=result, tool_call_id=tool_call["id"])
                )
                tool_results.append({"tool": tool_call["name"], "content": result})
                tool_step_count += 1
                last_action_signature = action_signature
                break

        if tool_call["name"] in CUSTOMER_SCOPED_WRITE_TOOLS and (
            not customer_resolution
            or customer_resolution.get("status") != "resolved"
        ):
            result, clarification_message = _blocked_customer_write(state, tool_call)
            tool_messages.extend(
                [
                    ToolMessage(content=result, tool_call_id=tool_call["id"]),
                    clarification_message,
                ]
            )
            tool_results.append({"tool": tool_call["name"], "content": result})
            return {
                "messages": tool_messages,
                "last_tool_result": {"tools": tool_results},
                "last_tool_succeeded": False,
                "customer_resolution": customer_resolution,
                "pending_human_input": clarification_message.tool_calls[0]["args"][
                    "question"
                ],
                "pending_human_input_type": "clarification",
                "status": RunStatus.WAITING_FOR_CLARIFICATION.value,
                "execution_stop_reason": None,
                "tool_step_count": tool_step_count,
                "last_action_signature": last_action_signature,
                "consecutive_action_count": consecutive_action_count,
                "last_failed_non_retryable_action": action_signature,
                "retry_counts": retry_counts,
                "pending_approval_action": None,
            }

        if (
            state.get("business_checks")
            and tool_call["name"] in {"list_tasks", "create_task"}
            and not _project_in_resolved_customer_scope(
                state, tool_call["args"].get("project_id")
            )
        ):
            result = json.dumps(
                {
                    "success": False,
                    "error_type": "project_scope_violation",
                    "retryable": False,
                    "message": (
                        f"project_id {tool_call['args'].get('project_id')} has not "
                        "been observed as an onboarding project of the resolved "
                        "customer. Use list_projects for this customer and use a "
                        "project_id from that result."
                    ),
                }
            )
            tool_messages.append(
                ToolMessage(content=result, tool_call_id=tool_call["id"])
            )
            tool_results.append({"tool": tool_call["name"], "content": result})
            tool_step_count += 1
            last_action_signature = action_signature
            break

        if (
            tool_call["name"] == "create_customer"
            and not _explicitly_requests_customer_creation(state["goal"])
        ):
            result = json.dumps(
                {
                    "success": False,
                    "error_type": "customer_creation_not_requested",
                    "retryable": False,
                    "message": (
                        "Customer creation was not requested. Use find_customer "
                        "to resolve the existing customer, then inspect its "
                        "onboarding state."
                    ),
                }
            )
            tool_messages.append(
                ToolMessage(content=result, tool_call_id=tool_call["id"])
            )
            tool_results.append({"tool": tool_call["name"], "content": result})
            continue

        if tool_call["name"] == "send_email":
            customer = await customer_context_for_recipient(
                state, tool_call["args"]["to"]
            )
            policy = evaluate_email(
                recipient=tool_call["args"]["to"],
                subject=tool_call["args"]["subject"],
                body=tool_call["args"]["body"],
                customer=customer,
            )
            pending_action = state.get("pending_approval_action")
            approved_exact_action = (
                pending_action is not None
                and pending_action["tool"] == tool_call["name"]
                and pending_action["args"] == tool_call["args"]
                and state.get("human_response", "").strip().lower() == "approve"
            )
            if policy["decision"] == DENY or (
                policy["decision"] != ALLOW and not approved_exact_action
            ):
                if policy["decision"] == APPROVAL_REQUIRED:
                    pending_action = {
                        "tool": tool_call["name"],
                        "args": dict(tool_call["args"]),
                        "reason": policy["reason"],
                        "tool_call_id": tool_call["id"],
                    }
                    result = json.dumps(
                        {
                            "success": False,
                            "error_type": "policy",
                            "retryable": False,
                            "policy_decision": policy["decision"],
                            "message": policy["reason"],
                        }
                    )
                    return {
                        "pending_approval_action": pending_action,
                        "last_tool_result": {
                            "tools": [{"tool": tool_call["name"], "content": result}]
                        },
                        "last_tool_succeeded": False,
                        "execution_stop_reason": "communication_approval_required",
                    }
                result = json.dumps(
                    {
                        "success": False,
                        "error_type": "policy",
                        "retryable": False,
                        "policy_decision": policy["decision"],
                        "message": policy["reason"],
                    }
                )
                tool_messages.append(
                    ToolMessage(content=result, tool_call_id=tool_call["id"])
                )
                tool_results.append({"tool": tool_call["name"], "content": result})
                execution_stop_reason = (
                    "communication_approval_required"
                    if policy["decision"] == APPROVAL_REQUIRED
                    else "communication_denied"
                )
                break

        if action_signature == last_action_signature and not same_retryable_action:
            blocked_result = {
                "success": False,
                "error_type": "execution_guard",
                "message": (
                    "Execution stopped because the same action was requested "
                    "repeatedly without an explicit retryable failure."
                ),
            }
            result = json.dumps(blocked_result)
            tool_messages.append(
                ToolMessage(content=result, tool_call_id=tool_call["id"])
            )
            tool_results.append({"tool": tool_call["name"], "content": result})
            execution_stop_reason = "repeated_action"
            break

        if same_retryable_action and retry_counts.get(action_signature, 0) >= MAX_RETRIES_PER_ACTION:
            blocked_result = {
                "success": False,
                "error_type": "transient_retry_exhausted",
                "retryable": False,
                "message": (
                    "Execution stopped after the maximum number of transient "
                    "retries for this action."
                ),
            }
            result = json.dumps(blocked_result)
            tool_messages.append(
                ToolMessage(content=result, tool_call_id=tool_call["id"])
            )
            tool_results.append({"tool": tool_call["name"], "content": result})
            execution_stop_reason = "transient_retry_exhausted"
            break

        if tool_step_count >= MAX_TOOL_STEPS:
            execution_stop_reason = "tool_step_limit"
            break

        cached_lookup = (
            _cached_customer_lookup(state, tool_call["args"].get("name", ""))
            if tool_call["name"] == "find_customer"
            else None
        )
        if cached_lookup is not None:
            tool_messages.append(
                ToolMessage(content=cached_lookup, tool_call_id=tool_call["id"])
            )
            tool_results.append({"tool": tool_call["name"], "content": cached_lookup})
            last_action_signature = action_signature
            continue

        if same_retryable_action:
            retry_counts[action_signature] = retry_counts.get(action_signature, 0) + 1

        try:
            result = await selected_tool.ainvoke(tool_call["args"])
        except Exception as error:
            result = _structured_tool_exception(error)
        tool_messages.append(
            ToolMessage(content=result, tool_call_id=tool_call["id"])
        )
        tool_results.append({"tool": tool_call["name"], "content": result})
        tool_step_count += 1
        last_action_signature = action_signature

        parsed_result = parse_tool_result(result)
        resolution_update = _customer_resolution_from_result(
            tool_call["name"], parsed_result
        )
        if resolution_update is not None:
            customer_resolution = _bind_customer_resolution(
                state,
                resolution_update,
                str(tool_call["args"].get("name", "")),
                parsed_result,
            )
        if parsed_result.get("retryable") is True and parsed_result.get("success") is False:
            if (
                same_retryable_action
                and retry_counts[action_signature] >= MAX_RETRIES_PER_ACTION
            ):
                execution_stop_reason = "transient_retry_exhausted"
                break
        if (
            parsed_result.get("success") is False
            and parsed_result.get("error_type") == "duplicate"
        ):
            last_failed_non_retryable_action = action_signature

        if tool_step_count >= MAX_TOOL_STEPS and execution_stop_reason is None:
            execution_stop_reason = "tool_step_limit"
            break

    successful = True
    for result in tool_results:
        parsed_result = parse_tool_result(result["content"])
        if parsed_result.get("success") is False:
            successful = False

    return {
        "messages": [*tool_messages, *_unexecuted_tool_messages(state, tool_messages)],
        "last_tool_result": {"tools": tool_results},
        "last_tool_succeeded": successful,
        "tool_step_count": tool_step_count,
        "last_action_signature": last_action_signature,
        "consecutive_action_count": consecutive_action_count,
        "last_failed_non_retryable_action": last_failed_non_retryable_action,
        "retry_counts": retry_counts,
        "execution_stop_reason": execution_stop_reason,
        "pending_approval_action": None,
        "customer_resolution": customer_resolution,
    }


def route_after_tool(state: AgentState) -> str:
    if state.get("pending_human_input_type") == "clarification":
        return "clarify"
    if state.get("pending_approval_action"):
        return "approval"
    if state["execution_stop_reason"]:
        return "answer"
    if state["tool_step_count"] >= MAX_TOOL_STEPS:
        return "answer"
    if (
        _should_run_invoice_reconciliation(state)
        and any(
            item.get("tool") == "get_invoice"
            and parse_tool_result(item.get("content", "")).get("success") is True
            for item in (state.get("last_tool_result") or {}).get("tools", [])
        )
    ):
        return "reconcile_invoice"
    # An ambiguous or absent result from the lookup just performed is enough
    # to require identity clarification.  Route directly to the existing
    # gate rather than letting the planner propose a dependent customer read.
    resolution = state.get("customer_resolution") or {}
    last_tools = (state.get("last_tool_result") or {}).get("tools", [])
    if (
        resolution.get("status") in {"ambiguous", "not_found"}
        and any(item.get("tool") == "find_customer" for item in last_tools)
    ):
        return "customer_resolution_gate"
    # A batch can contain both a successful onboarding write and an unrelated
    # failed call.  The batch-level flag records the latter, but must not skip
    # read-back verification of the persisted project or task.
    if state["last_tool_result"] and _last_onboarding_writes(state):
        return "verify_onboarding"
    if (
        state["last_tool_succeeded"]
        and state["last_tool_result"]
        and any(
            item["tool"] == "send_email"
            and parse_tool_result(item["content"]).get("success") is True
            for item in state["last_tool_result"]["tools"]
        )
    ):
        return "verify"
    if (
        state["last_tool_succeeded"]
        and state["last_tool_result"]
        and any(
            item["tool"] == "log_invoice"
            and parse_tool_result(item["content"]).get("success") is True
            for item in state["last_tool_result"]["tools"]
        )
    ):
        return "verify_invoice"
    return "decide"


def route_after_verification(state: AgentState) -> str:
    if state["verification_result"]["status"] == "PASS":
        return "answer"
    if state["verification_result"].get("backend") == "gmail":
        return "answer"
    if state.get("verification_repair_count", 0) >= MAX_VERIFICATION_REPAIRS:
        return "answer"
    if state["verification_result"]["reason"] in {
        "recipient",
        "subject",
        "body",
        "status",
    }:
        return "repair"
    return "answer"


def route_after_onboarding_verification(state: AgentState) -> str:
    """Continue planning after a verified write; never repair a failed write."""
    if state.get("onboarding_verification_result", {}).get("status") == "PASS":
        return "decide"
    return "answer"


def route_after_approval(state: AgentState) -> str:
    if state["execution_stop_reason"]:
        return "answer"
    return "execute_tool"


def approval(state: AgentState) -> dict:
    """Pause for an explicit decision about one exact proposed email."""
    action = state.get("pending_approval_action")
    if not action:
        return {
            "execution_stop_reason": "approval_missing_action",
            "error": "Approval could not be associated with a pending action.",
        }

    response = interrupt(
        {
            "type": "approval",
            "tool": action["tool"],
            "arguments": action["args"],
            "reason": action["reason"],
            "message": (
                "Approval required for this exact communication. "
                "Resume with 'approve' or 'reject'."
            ),
        }
    )
    decision = str(response).strip().lower()
    if decision == "approve":
        return {"human_response": decision, "execution_stop_reason": None}
    if decision == "reject":
        result = json.dumps(
            {
                "success": False,
                "error_type": "approval_rejected",
                "retryable": False,
                "message": "The human rejected the proposed email.",
            }
        )
        return {
            "messages": [
                ToolMessage(
                    content=result,
                    tool_call_id=action["tool_call_id"],
                )
            ],
            "last_tool_result": {
                "tools": [{"tool": action["tool"], "content": result}]
            },
            "last_tool_succeeded": False,
            "execution_stop_reason": "communication_approval_rejected",
            "pending_approval_action": None,
            "human_response": decision,
        }

    result = json.dumps(
        {
            "success": False,
            "error_type": "approval_invalid",
            "retryable": False,
            "message": "Approval response must be 'approve' or 'reject'.",
        }
    )
    return {
        "messages": [
            ToolMessage(
                content=result,
                tool_call_id=action["tool_call_id"],
            )
        ],
        "last_tool_result": {"tools": [{"tool": action["tool"], "content": result}]},
        "last_tool_succeeded": False,
        "execution_stop_reason": "communication_approval_invalid",
        "pending_approval_action": None,
        "human_response": decision,
    }


def _last_onboarding_writes(state: AgentState) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return onboarding_write_verification.last_onboarding_writes(
        state, parse_tool_result, ONBOARDING_WRITE_TOOLS
    )


def _resolved_customer_for_onboarding_verification(
    state: AgentState, customer_name: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    return onboarding_write_verification._resolved_customer(
        state, customer_name, _resolution_matches_reference
    )


def _observed_project_for_customer(
    state: AgentState, project_id: int, customer_id: int
) -> bool:
    return onboarding_write_verification._observed_project(
        state, project_id, customer_id, parse_tool_result
    )


async def _verify_created_project(
    state: AgentState, action: dict[str, Any], execution: dict[str, Any]
) -> dict[str, Any]:
    return await onboarding_write_verification.verify_created_project(
        state,
        action,
        execution,
        parse_tool_result=parse_tool_result,
        call_list_projects=call_list_projects,
        resolution_matches_reference=_resolution_matches_reference,
    )


async def _verify_created_task(
    state: AgentState, action: dict[str, Any], execution: dict[str, Any]
) -> dict[str, Any]:
    return await onboarding_write_verification.verify_created_task(
        state,
        action,
        execution,
        parse_tool_result=parse_tool_result,
        call_list_projects=call_list_projects,
        call_list_tasks=call_list_tasks,
        resolution_matches_reference=_resolution_matches_reference,
    )


async def verify_onboarding(state: AgentState) -> dict:
    return await onboarding_write_verification.verify_onboarding(
        state,
        parse_tool_result=parse_tool_result,
        call_list_projects=call_list_projects,
        call_list_tasks=call_list_tasks,
        resolution_matches_reference=_resolution_matches_reference,
        onboarding_write_tools=ONBOARDING_WRITE_TOOLS,
    )


def _latest_email_action(state: AgentState) -> dict[str, Any] | None:
    return communication_verification.latest_email_action(state)


async def verify(state: AgentState) -> dict:
    return await communication_verification.verify(
        state,
        parse_tool_result=parse_tool_result,
        call_get_email=call_get_email,
    )


def _latest_invoice_action(state: AgentState) -> dict[str, Any] | None:
    for message in reversed(state["messages"]):
        if not isinstance(message, AIMessage):
            continue
        for tool_call in reversed(message.tool_calls):
            if tool_call["name"] == "log_invoice":
                return tool_call
    return None


async def verify_invoice(state: AgentState) -> dict:
    """Independently read a logged invoice and compare it with the intended action."""
    action = _latest_invoice_action(state)
    tool_result = next(
        (
            item
            for item in reversed(state["last_tool_result"]["tools"])
            if item["tool"] == "log_invoice"
        ),
        None,
    )
    if action is None or tool_result is None:
        return {
            "invoice_verification_result": {
                "status": "FAIL",
                "reason": "missing_execution_evidence",
            },
            "execution_stop_reason": "invoice_verification_failed",
        }

    execution = parse_tool_result(tool_result["content"])
    invoice_id = execution.get("invoice_id")
    customer_id = execution.get("customer_id")
    if not isinstance(invoice_id, int) or not isinstance(customer_id, int):
        return {
            "invoice_verification_result": {
                "status": "FAIL",
                "reason": "missing_invoice_identifier",
            },
            "execution_stop_reason": "invoice_verification_failed",
        }

    try:
        actual_result = parse_tool_result(await call_get_invoice(invoice_id))
    except Exception:
        actual_result = {}
    actual = actual_result.get("invoice")
    expected = {
        "customer_id": customer_id,
        "customer_name": action["args"]["customer_name"],
        "invoice_number": action["args"]["invoice_number"],
        "amount": str(action["args"]["amount"]),
        "due_date": action["args"]["due_date"],
        "revision_number": action["args"].get("revision_number", 0),
        "status": action["args"].get("status", "active"),
        "supersedes_invoice_number": action["args"].get("supersedes_invoice_number"),
    }
    if not actual_result.get("success") or not isinstance(actual, dict):
        evidence = {"status": "FAIL", "reason": "invoice_not_found", "invoice_id": invoice_id}
    else:
        mismatches = {
            field: {"expected": value, "actual": actual.get(field)}
            for field, value in expected.items()
            if (
                str(actual.get(field)).casefold() != str(value).casefold()
                if field == "customer_name"
                else actual.get(field) != value
            )
        }
        evidence = (
            {"status": "FAIL", "reason": next(iter(mismatches)), "mismatches": mismatches}
            if mismatches
            else {"status": "PASS", "invoice_id": invoice_id, "customer_id": customer_id}
        )
    return {
        "invoice_verification_result": evidence,
        "execution_stop_reason": (
            "invoice_verification_failed" if evidence["status"] == "FAIL" else None
        ),
    }


def _is_invoice_reconciliation_goal(state: AgentState) -> bool:
    return invoice_reconciliation.is_invoice_reconciliation_goal(state)


def _should_run_invoice_reconciliation(state: AgentState) -> bool:
    return invoice_reconciliation.should_run_invoice_reconciliation(state)


def _latest_tool_observation(
    state: AgentState, tool_name: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    return invoice_reconciliation.latest_tool_observation(
        state, tool_name, parse_tool_result
    )


def _document_invoice_value(content: str, label: str) -> str | None:
    return invoice_reconciliation.document_invoice_value(content, label)


def _normalized_amount(value: Any) -> str | None:
    return invoice_reconciliation.normalized_amount(value)


def _normalized_document_date(value: str | None) -> str | None:
    return invoice_reconciliation.normalized_document_date(value)


async def reconcile_invoice(state: AgentState) -> dict:
    return await invoice_reconciliation.reconcile_invoice(
        state,
        parse_tool_result=parse_tool_result,
        call_get_invoice=call_get_invoice,
        call_search_documents=call_search_documents,
        call_read_document=call_read_document,
    )


def repair(state: AgentState) -> dict:
    """Create one bounded corrective email proposal for an observed mismatch."""
    evidence = state.get("verification_result")
    action = _latest_email_action(state)
    if (
        not evidence
        or evidence.get("status") != "FAIL"
        or evidence.get("reason")
        not in {"recipient", "subject", "body", "status"}
        or action is None
    ):
        return {
            "execution_stop_reason": "communication_verification_failed",
        }

    repair_count = state.get("verification_repair_count", 0) + 1
    repair_call = {
        "name": "send_email",
        "args": dict(action["args"]),
        "id": f"{action['id']}-repair-{repair_count}",
        "type": "tool_call",
    }
    return {
        "messages": [AIMessage(content="", tool_calls=[repair_call])],
        "verification_repair_count": repair_count,
        "verification_result": None,
        "execution_stop_reason": None,
        "last_action_signature": None,
        "consecutive_action_count": 0,
    }


def _verified_onboarding_claims(state: AgentState) -> dict[str, list[int]]:
    """Return only IDs backed by a successful persisted-write verification."""
    evidence = state.get("onboarding_verification_result") or {}
    if evidence.get("status") != "PASS":
        return {"projects": [], "tasks": []}
    writes = evidence.get("writes")
    if not isinstance(writes, list):
        return {"projects": [], "tasks": []}
    projects = [
        item["project_id"]
        for item in writes
        if isinstance(item, dict)
        and item.get("kind") == "project"
        and isinstance(item.get("project_id"), int)
    ]
    tasks = [
        item["task_id"]
        for item in writes
        if isinstance(item, dict)
        and item.get("kind") == "task"
        and isinstance(item.get("task_id"), int)
    ]
    return {"projects": projects, "tasks": tasks}


def _onboarding_verification_instruction(state: AgentState) -> SystemMessage:
    """Constrain creation claims to independently verified onboarding writes."""
    claims = _verified_onboarding_claims(state)
    return SystemMessage(
        content=(
            "Report the deterministic onboarding write verification "
            "accurately. A PASS proves only that the created project "
            "or task was read back with the expected persisted fields; "
            "it does not prove all contract or SOP requirements are complete. "
            f"The only verified newly created project IDs are {claims['projects']}; "
            f"the only verified newly created task IDs are {claims['tasks']}. "
            "Do not say a project or task was created unless its ID appears "
            "in that matching list. If a project was merely listed or reused, "
            "say it was reused, not created."
        )
    )


def _project_in_resolved_customer_scope(state: AgentState, project_id: Any) -> bool:
    """True only if this run observed the project as belonging to the resolved customer."""
    customer = (state.get("customer_resolution") or {}).get("customer")
    if (
        not isinstance(project_id, int)
        or not isinstance(customer, dict)
        or not isinstance(customer.get("id"), int)
    ):
        return False
    return _observed_project_for_customer(state, project_id, customer["id"])


def _should_run_completion_check(state: AgentState) -> bool:
    """Run the independent completeness review once the model stops proposing work."""
    if not state.get("business_checks"):
        return False
    if "onboard" not in state["goal"].casefold():
        return False
    if state.get("execution_stop_reason"):
        return False
    if state.get("completion_check_count", 0) > MAX_COMPLETION_REPAIRS:
        return False
    resolution = state.get("customer_resolution") or {}
    customer = resolution.get("customer")
    return (
        resolution.get("status") == "resolved"
        and isinstance(customer, dict)
        and bool(str(customer.get("legal_name") or "").strip())
    )


def _json_object(text: str) -> dict[str, Any]:
    return onboarding_verification.json_object(text)


async def _completion_documents(state: AgentState, legal_name: str) -> dict[str, str]:
    return await onboarding_verification.completion_documents(
        _search_results(state),
        legal_name,
        parse_tool_result=parse_tool_result,
        call_search_documents=call_search_documents,
        call_read_document=call_read_document,
        reference_documents=COMPLETION_REFERENCE_DOCS,
        max_document_chars=MAX_COMPLETION_DOC_CHARS,
    )


async def _completion_actual_tasks(
    legal_name: str, project_ids: set[int]
) -> list[dict[str, Any]]:
    return await onboarding_verification.completion_actual_tasks(
        legal_name,
        project_ids,
        parse_tool_result=parse_tool_result,
        call_list_projects=call_list_projects,
        call_list_tasks=call_list_tasks,
    )


_COMPLETION_GENERIC_TERMS = onboarding_verification._COMPLETION_GENERIC_TERMS


def _completion_terms(text: Any) -> set[str]:
    return onboarding_verification.completion_terms(text)


def _task_coverage_evidence(item: str, task: dict[str, Any]) -> dict[str, Any]:
    return onboarding_verification.task_coverage_evidence(item, task)


async def _review_requirements(
    legal_name: str, documents: dict[str, str], tasks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return await onboarding_verification.review_requirements(
        legal_name,
        documents,
        tasks,
        get_llm=get_llm,
        max_items=MAX_COMPLETION_ITEMS,
    )


async def verify_requirements(state: AgentState) -> dict:
    return await onboarding_verification.verify_requirements(
        state,
        search_results=_search_results(state),
        project_ids=_run_project_ids(
            state, state["customer_resolution"]["customer"].get("id")
        ),
        parse_tool_result=parse_tool_result,
        call_search_documents=call_search_documents,
        call_read_document=call_read_document,
        call_list_projects=call_list_projects,
        call_list_tasks=call_list_tasks,
        get_llm=get_llm,
        reference_documents=COMPLETION_REFERENCE_DOCS,
        max_document_chars=MAX_COMPLETION_DOC_CHARS,
        max_items=MAX_COMPLETION_ITEMS,
        max_repairs=MAX_COMPLETION_REPAIRS,
    )


def route_after_completion_check(state: AgentState) -> str:
    result = state.get("onboarding_completion_result") or {}
    if result.get("status") != "FAIL":
        return END
    return "answer" if state.get("execution_stop_reason") else "decide"


def _format_reconciliation_value(value: Any) -> str:
    return invoice_reconciliation.format_reconciliation_value(value)


def _invoice_reconciliation_final_response(
    evidence: dict[str, Any], execution_stop_reason: str | None
) -> str:
    return invoice_reconciliation.invoice_reconciliation_final_response(
        evidence, execution_stop_reason
    )


async def answer(state: AgentState) -> dict:
    """Ask the model for the final answer after the optional tool call."""
    reconciliation = state.get("invoice_reconciliation_result")
    if isinstance(reconciliation, dict) and reconciliation.get("status") in {
        "VERIFIED",
        "FAILED",
        "INSUFFICIENT_EVIDENCE",
    }:
        # Reconciliation is a read-only, deterministic result. Its structured
        # evidence is authoritative, so a final model response cannot reverse
        # a failed or insufficient outcome in prose.
        return {
            "messages": [
                AIMessage(
                    content=_invoice_reconciliation_final_response(
                        reconciliation, state.get("execution_stop_reason")
                    )
                )
            ]
        }
    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    if state["execution_stop_reason"]:
        messages.append(
            SystemMessage(
                content=(
                    "Execution was stopped by a runtime safety guard. Do not "
                    "claim that the requested operation completed. Clearly "
                    "explain the stop reason and the last observed result."
                )
            )
        )
    if state.get("verification_result"):
        messages.append(
            SystemMessage(
                content=(
                    "Report the deterministic verification result accurately. "
                    "A PASS means the configured backend's resulting email "
                    "record matched the intended email. Do not claim external "
                    "delivery unless the tool result explicitly identifies "
                    "Gmail or another external backend."
                )
            )
        )
    if state.get("onboarding_verification_result"):
        messages.append(_onboarding_verification_instruction(state))
    if state.get("invoice_verification_result"):
        messages.append(
            SystemMessage(
                content=(
                    "Report the deterministic invoice verification result accurately. "
                    "Do not claim an invoice was logged when verification failed."
                )
            )
        )
    if state.get("invoice_reconciliation_result"):
        messages.append(
            SystemMessage(
                content=(
                    "Report the read-only invoice reconciliation accurately. "
                    "VERIFIED means the selected active persisted invoice matched "
                    "the available internal invoice document on the listed fields. "
                    "FAILED or INSUFFICIENT_EVIDENCE must not be described as a "
                    "verified business outcome, and no commercial or legal correctness "
                    "claim is supported by this check."
                )
            )
        )
    messages.extend(state["messages"])
    response = await get_llm().ainvoke(
        messages
    )
    return {"messages": [response]}


builder = StateGraph(AgentState)
builder.add_node("decide", decide)
builder.add_node("retrieve_onboarding_knowledge", retrieve_onboarding_knowledge)
builder.add_node("customer_resolution_gate", customer_resolution_gate)
builder.add_node("execute_tool", execute_tool)
builder.add_node("clarify", clarify)
builder.add_node("approval", approval)
builder.add_node("verify", verify)
builder.add_node("verify_onboarding", verify_onboarding)
builder.add_node("verify_invoice", verify_invoice)
builder.add_node("reconcile_invoice", reconcile_invoice)
builder.add_node("repair", repair)
builder.add_node("verify_requirements", verify_requirements)
builder.add_node("answer", answer)
builder.add_edge(START, "decide")
builder.add_conditional_edges(
    "decide",
    route_after_decision,
    {"execute_tool": "execute_tool", "retrieve_onboarding_knowledge": "retrieve_onboarding_knowledge", "customer_resolution_gate": "customer_resolution_gate",
    "clarify": "clarify",
    "verify_requirements": "verify_requirements",
    "reconcile_invoice": "reconcile_invoice",
    END: END},
)
builder.add_conditional_edges(
    "retrieve_onboarding_knowledge",
    lambda state: "answer" if state.get("execution_stop_reason") else "decide",
    {"answer": "answer", "decide": "decide"},
)
builder.add_conditional_edges(
    "customer_resolution_gate",
    lambda state: (
        "execute_tool"
        if (state.get("customer_resolution") or {}).get("status") == "resolved"
        else "clarify"
    ),
    {"execute_tool": "execute_tool", "clarify": "clarify"},
)
builder.add_conditional_edges(
    "execute_tool",
    route_after_tool,
    {
        "decide": "decide",
        "customer_resolution_gate": "customer_resolution_gate",
        "clarify": "clarify",
        "approval": "approval",
        "verify": "verify",
        "verify_onboarding": "verify_onboarding",
        "verify_invoice": "verify_invoice",
        "reconcile_invoice": "reconcile_invoice",
        "answer": "answer",
    },
)
builder.add_conditional_edges(
    "approval",
    route_after_approval,
    {"execute_tool": "execute_tool", "answer": "answer"},
)
builder.add_edge("verify_invoice", "answer")
builder.add_edge("reconcile_invoice", "answer")
builder.add_conditional_edges(
    "verify",
    route_after_verification,
    {"repair": "repair", "answer": "answer"},
)
builder.add_conditional_edges(
    "verify_onboarding",
    route_after_onboarding_verification,
    {"decide": "decide", "answer": "answer"},
)
builder.add_edge("repair", "execute_tool")
builder.add_conditional_edges(
    "verify_requirements",
    route_after_completion_check,
    {"decide": "decide", "answer": "answer", END: END},
)
builder.add_edge("clarify", "decide")
builder.add_edge("answer", END)

compiled_graph = builder.compile(checkpointer=InMemorySaver())


class ConfiguredGraph:
    """Keep legacy callers working while allowing explicit HITL thread IDs."""

    def __init__(self, runnable):
        self._runnable = runnable

    @staticmethod
    def _checkpoint_input(input_state):
        if isinstance(input_state, dict) and isinstance(
            input_state.get("status"), RunStatus
        ):
            input_state = dict(input_state)
            input_state["status"] = input_state["status"].value
        return input_state

    async def ainvoke(self, input_state, config=None, **kwargs):
        if config is None:
            config = {"configurable": {"thread_id": input_state["run_id"]}}
        return await self._runnable.ainvoke(
            self._checkpoint_input(input_state), config=config, **kwargs
        )

    async def astream(self, input_state, config=None, **kwargs):
        """Stream native graph updates for thin presentation adapters."""
        if config is None:
            if not isinstance(input_state, dict):
                raise ValueError("A config is required when streaming a graph resume.")
            config = {"configurable": {"thread_id": input_state["run_id"]}}
        async for update in self._runnable.astream(
            self._checkpoint_input(input_state), config=config, **kwargs
        ):
            yield update

    async def aget_state(self, config):
        """Expose a checkpoint snapshot without changing graph behavior."""
        return await self._runnable.aget_state(config)

    def invoke(self, input_state, config=None, **kwargs):
        if config is None:
            config = {"configurable": {"thread_id": input_state["run_id"]}}
        return self._runnable.invoke(
            self._checkpoint_input(input_state), config=config, **kwargs
        )

    def get_graph(self):
        return self._runnable.get_graph()


graph = ConfiguredGraph(compiled_graph)
