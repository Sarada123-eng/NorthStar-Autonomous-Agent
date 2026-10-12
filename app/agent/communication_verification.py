from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage

from app.agent.state import AgentState


ParseToolResult = Callable[[str], dict[str, Any]]
AsyncToolCall = Callable[..., Awaitable[str]]


def latest_email_action(state: AgentState) -> dict[str, Any] | None:
    for message in reversed(state["messages"]):
        if not isinstance(message, AIMessage):
            continue
        for tool_call in reversed(message.tool_calls):
            if tool_call["name"] == "send_email":
                return tool_call
    return None


async def verify(
    state: AgentState,
    *,
    parse_tool_result: ParseToolResult,
    call_get_email: AsyncToolCall,
) -> dict:
    """Read the configured backend email and compare it with the intended action."""
    action = latest_email_action(state)
    tool_result = next(
        (
            item
            for item in reversed(state["last_tool_result"]["tools"])
            if item["tool"] == "send_email"
        ),
        None,
    )
    if action is None or tool_result is None:
        return {
            "verification_result": {
                "status": "FAIL",
                "reason": "missing_execution_evidence",
            },
            "execution_stop_reason": "communication_verification_failed",
        }

    execution = parse_tool_result(tool_result["content"])
    email_id = execution.get("email_id")
    backend = execution.get("backend", "mock")
    if not isinstance(email_id, (int, str)):
        return {
            "verification_result": {
                "status": "FAIL",
                "reason": "missing_email_id",
                "backend": backend,
            },
            "execution_stop_reason": "communication_verification_failed",
        }

    actual_result = parse_tool_result(await call_get_email(email_id))
    actual = actual_result.get("email")
    expected = {
        "recipient": action["args"]["to"],
        "subject": action["args"]["subject"],
        "body": action["args"]["body"],
        "status": "sent",
    }
    if not actual_result.get("success") or not isinstance(actual, dict):
        evidence = {
            "status": "FAIL",
            "reason": "email_not_found",
            "email_id": email_id,
            "backend": backend,
        }
    else:
        mismatches = {
            field: {"expected": expected[field], "actual": actual.get(field)}
            for field in expected
            if actual.get(field) != expected[field]
        }
        evidence = (
            {
                "status": "FAIL",
                "reason": next(iter(mismatches)),
                "mismatches": mismatches,
                "backend": backend,
            }
            if mismatches
            else {"status": "PASS", "email_id": email_id, "backend": backend}
        )

    return {
        "verification_result": evidence,
        "execution_stop_reason": (
            "communication_verification_failed"
            if evidence["status"] == "FAIL"
            else None
        ),
    }
