import json
import re
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.state import AgentState


ParseToolResult = Callable[[str], dict[str, Any]]
AsyncToolCall = Callable[..., Awaitable[str]]


def resolution_from_result(
    tool_name: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    if tool_name != "find_customer":
        return None
    explicit = result.get("resolution")
    if isinstance(explicit, dict) and explicit.get("status"):
        resolved = dict(explicit)
        customer = result.get("customer")
        if isinstance(customer, dict) and "customer" not in resolved:
            resolved["customer"] = customer
        return resolved
    if result.get("found") is True and isinstance(result.get("customer"), dict):
        return {
            "status": "resolved",
            "reference": result["customer"].get("name"),
            "customer": result["customer"],
        }
    if result.get("found") is False:
        return {"status": "not_found"}
    return {"status": "unresolved"}


def normalize_reference(value: str) -> str:
    """Match the database's legal-name comparison normalization."""
    cleaned = re.sub(r"[.]+$", "", value.strip())
    return re.sub(r"\s+", " ", cleaned).casefold()


def bind_resolution(
    state: AgentState,
    resolution: dict[str, Any] | None,
    requested_reference: str,
    lookup_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Attach same-run provenance to a successful customer lookup."""
    if not resolution or resolution.get("status") != "resolved":
        return resolution

    bound = dict(resolution)
    customer = bound.get("customer")
    if not isinstance(customer, dict):
        customer = lookup_result.get("customer")
    if isinstance(customer, dict):
        bound["customer"] = customer
    bound["run_id"] = state["run_id"]
    bound["normalized_reference"] = normalize_reference(requested_reference)
    bound["lookup_result"] = lookup_result
    return bound


def customer_reference_for_current_actions(
    state: AgentState, scoped_action_tools: set[str]
) -> str | None:
    """Return an explicit customer name requested by the current tool calls."""
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage):
        return explicit_customer_reference(state["goal"])
    for tool_call in last_message.tool_calls:
        if tool_call["name"] not in scoped_action_tools:
            continue
        reference = tool_call["args"].get("customer_name")
        if isinstance(reference, str) and reference.strip():
            return reference
    return explicit_customer_reference(state["goal"])


def resolution_matches_reference(
    state: AgentState,
    resolution: dict[str, Any] | None,
    requested_reference: str | None,
) -> bool:
    """Allow reuse only for a resolved identity from this run and customer."""
    if (
        not resolution
        or resolution.get("status") != "resolved"
        or resolution.get("run_id") != state["run_id"]
    ):
        return False
    if not requested_reference:
        return True

    requested = normalize_reference(requested_reference)
    known_references = [
        resolution.get("normalized_reference"),
        resolution.get("reference"),
    ]
    customer = resolution.get("customer")
    if isinstance(customer, dict):
        known_references.extend([customer.get("name"), customer.get("legal_name")])
    return any(
        isinstance(reference, str)
        and normalize_reference(reference) == requested
        for reference in known_references
    )


def cached_lookup(
    state: AgentState,
    customer_name: str,
) -> str | None:
    """Return same-run evidence for an identical resolved lookup, if any."""
    resolution = state.get("customer_resolution")
    if not resolution_matches_reference(state, resolution, customer_name):
        return None
    lookup_result = resolution.get("lookup_result")
    if not isinstance(lookup_result, dict) or lookup_result.get("found") is not True:
        return None
    return json.dumps(lookup_result)


def explicit_customer_reference(goal: str) -> str | None:
    """Extract a likely named customer only when the goal states one."""
    markers = (
        r"\bfor\s+(?:the\s+)?",
        r"\bonboard\s+",
        r"\bexact\s+legal\s+name\s+",
        r"\bcustomer\s+(?:named|called)\s+",
    )
    sentence_boundary = re.compile(r"[.]\s+(?=[A-Z])")
    clause_boundary = re.compile(
        r"\s+(?:verify|confirm|check|do\s+not|report|compare|ensure|retrieve"
        r"|inspect|and\s+(?:verify|confirm|check|report|do\s+not))\b",
        flags=re.IGNORECASE,
    )
    for marker in markers:
        matches = list(re.finditer(marker, goal, flags=re.IGNORECASE))
        if not matches:
            continue
        tail = goal[matches[-1].end():]
        sent_match = sentence_boundary.search(tail)
        if sent_match:
            tail = tail[:sent_match.start()]
        clause_match = clause_boundary.search(tail)
        if clause_match:
            tail = tail[:clause_match.start()]
        reference = tail.strip(" .,!?:;")
        reference = re.sub(
            r"\s+(?:project|customer|onboarding project)\s*$",
            "",
            reference,
            flags=re.IGNORECASE,
        ).strip(" .,!?:;")
        if reference and " " in reference and re.match(r"[A-Z]", reference):
            return reference
    return None


def resolution_question(
    state: AgentState, parse_tool_result: ParseToolResult
) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, ToolMessage):
            result = parse_tool_result(message.content)
            resolution = result.get("resolution")
            if isinstance(resolution, dict) and resolution.get("status") == "ambiguous":
                candidates = resolution.get("candidates", [])
                if candidates:
                    return (
                        "I found multiple possible customer entities: "
                        + "; ".join(str(candidate) for candidate in candidates)
                        + ". Which one should I use?"
                    )
    return (
        "Which specific customer entity should I use? Please provide the "
        "customer's full legal name or another distinguishing identifier."
    )


def blocked_customer_write(
    state: AgentState,
    tool_call: dict[str, Any],
    parse_tool_result: ParseToolResult,
) -> tuple[str, AIMessage]:
    question = resolution_question(state, parse_tool_result)
    result = json.dumps(
        {
            "success": False,
            "error_type": "customer_resolution_required",
            "retryable": False,
            "resolution_status": "unresolved",
            "message": question,
        }
    )
    clarification_call = {
        "name": "request_clarification",
        "args": {"question": question},
        "id": f"{tool_call['id']}-customer-resolution",
        "type": "tool_call",
    }
    return result, AIMessage(content="", tool_calls=[clarification_call])


def latest_customer_context(
    state: AgentState, parse_tool_result: ParseToolResult
) -> dict[str, Any] | None:
    for message in reversed(state["messages"]):
        if not isinstance(message, ToolMessage):
            continue
        result = parse_tool_result(message.content)
        customer = result.get("customer")
        if isinstance(customer, dict):
            return customer
    return None


async def customer_context_for_recipient(
    state: AgentState,
    recipient: str,
    *,
    parse_tool_result: ParseToolResult,
    call_find_customer_by_contact: AsyncToolCall,
) -> dict[str, Any] | None:
    customer = latest_customer_context(state, parse_tool_result)
    if customer is not None:
        recipient_clean = recipient.strip().lower()
        verified_contacts = [
            email.lower()
            for field in ("billing_contact", "technical_contact")
            for email in re.findall(
                r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", str(customer.get(field, ""))
            )
        ]
        if recipient_clean in verified_contacts:
            return customer
    result = parse_tool_result(await call_find_customer_by_contact(recipient))
    customer = result.get("customer")
    return customer if isinstance(customer, dict) else None


def canonical_customer_args(
    state: AgentState,
    tool_call: dict[str, Any],
    *,
    scoped_customer_tools: set[str],
    resolution_matches: Callable[
        [AgentState, dict[str, Any] | None, str | None], bool
    ],
) -> dict[str, Any]:
    """Send customer-scoped calls under the resolved customer's legal name."""
    if not state.get("business_checks") or tool_call["name"] not in scoped_customer_tools:
        return tool_call
    reference = tool_call["args"].get("customer_name")
    resolution = state.get("customer_resolution")
    customer = resolution.get("customer") if resolution else None
    legal_name = customer.get("legal_name") if isinstance(customer, dict) else None
    if (
        not isinstance(reference, str)
        or not legal_name
        or not resolution_matches(state, resolution, reference)
    ):
        return tool_call
    return {**tool_call, "args": {**tool_call["args"], "customer_name": legal_name}}
