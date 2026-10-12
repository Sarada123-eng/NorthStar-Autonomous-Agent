"""Opt-in observable-action diagnostics for CLI smoke investigations."""

import json
import os
import re
import sys
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


_SENSITIVE_KEY = re.compile(
    r"(api.?key|authorization|credential|token|password|secret|private.?key|body|content)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"([\w.+-])[\w.+-]*@([\w.-]+)")


def diagnostics_enabled() -> bool:
    return os.getenv("NORTHSTAR_DIAGNOSTICS") == "1"


def _sanitize(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _EMAIL.sub(r"\1***@\2", value)
    return value


def _tool_result(content: str) -> Any:
    try:
        return _sanitize(json.loads(content))
    except (TypeError, json.JSONDecodeError):
        return {"unstructured_result": "[omitted]"}


def build_diagnostic_report(state: dict, transitions: list[dict[str, Any]]) -> dict[str, Any]:
    """Return observable actions only; never include prompts or model reasoning."""
    calls: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for message in state.get("messages", []):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                item = {
                    "event": "tool_call",
                    "tool": call["name"],
                    "tool_call_id": call["id"],
                    "arguments": _sanitize(call.get("args", {})),
                }
                calls[call["id"]] = item
                events.append(item)
        elif isinstance(message, ToolMessage):
            call = calls.get(message.tool_call_id, {})
            events.append(
                {
                    "event": "tool_result",
                    "tool": call.get("tool", "unknown"),
                    "tool_call_id": message.tool_call_id,
                    "result": _tool_result(message.content),
                }
            )
    return {
        "run_id": state.get("run_id"),
        "transitions": _sanitize(transitions),
        "events": events,
        "execution_stop_reason": state.get("execution_stop_reason"),
        "verification_result": _sanitize(state.get("verification_result")),
        "onboarding_verification_result": _sanitize(
            state.get("onboarding_verification_result")
        ),
        "invoice_verification_result": _sanitize(state.get("invoice_verification_result")),
        "invoice_reconciliation_result": _sanitize(
            state.get("invoice_reconciliation_result")
        ),
        "completion_check_count": state.get("completion_check_count"),
        "onboarding_completion_result": _sanitize(
            state.get("onboarding_completion_result")
        ),
    }


def emit_diagnostics(state: dict, transitions: list[dict[str, Any]]) -> bool:
    """Emit one sanitized JSON record to stderr only when explicitly enabled."""
    if not diagnostics_enabled():
        return False
    print(json.dumps(build_diagnostic_report(state, transitions), default=str), file=sys.stderr)
    return True
