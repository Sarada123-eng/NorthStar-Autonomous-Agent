import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.state import AgentState


ParseToolResult = Callable[[str], dict[str, Any]]
AsyncToolCall = Callable[..., Awaitable[str]]


def is_invoice_reconciliation_goal(state: AgentState) -> bool:
    """Recognize the narrow, read-only invoice consistency workflow."""
    goal = state.get("goal", "").casefold()
    return "invoice" in goal and any(
        word in goal for word in ("verify", "verification", "reconcile", "reconciliation")
    )


def should_run_invoice_reconciliation(state: AgentState) -> bool:
    return (
        is_invoice_reconciliation_goal(state)
        and state.get("invoice_reconciliation_result") is None
        and not state.get("execution_stop_reason")
    )


def latest_tool_observation(
    state: AgentState, tool_name: str, parse_tool_result: ParseToolResult
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the latest successful tool payload and its original call."""
    calls: dict[str, dict[str, Any]] = {}
    observations: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for message in state.get("messages", []):
        if isinstance(message, AIMessage):
            calls.update({call["id"]: call for call in message.tool_calls})
        elif isinstance(message, ToolMessage):
            call = calls.get(message.tool_call_id)
            if call and call.get("name") == tool_name:
                payload = parse_tool_result(message.content)
                if payload.get("success") is True:
                    observations.append((payload, call))
    return observations[-1] if observations else (None, None)


def document_invoice_value(content: str, label: str) -> str | None:
    match = re.search(
        rf"\*\*{re.escape(label)}\*\*\s*\|\s*([^|\n]+)",
        content,
        re.IGNORECASE,
    )
    return match.group(1).strip().strip("*").strip() if match else None


def normalized_amount(value: Any) -> str | None:
    try:
        return format(Decimal(re.sub(r"[^0-9.-]", "", str(value))), ".2f")
    except (InvalidOperation, ValueError):
        return None


def normalized_document_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


async def reconcile_invoice(
    state: AgentState,
    *,
    parse_tool_result: ParseToolResult,
    call_get_invoice: AsyncToolCall,
    call_search_documents: AsyncToolCall,
    call_read_document: AsyncToolCall,
) -> dict:
    """Read-only consistency check of an active invoice and its source document.

    This is intentionally separate from invoice write verification. Selection
    evidence is read from the graph state exactly as before; this node does not
    trigger another listing call.
    """
    listed, _ = latest_tool_observation(state, "list_invoices", parse_tool_result)
    selected, selected_call = latest_tool_observation(
        state, "get_invoice", parse_tool_result
    )
    if listed is None or selected is None or selected_call is None:
        evidence = {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "missing_invoice_selection_evidence",
            "fields": [],
        }
    else:
        current = listed.get("current_invoice")
        selected_id = selected_call.get("args", {}).get("invoice_id")
        invoice = selected.get("invoice")
        if not isinstance(current, dict) or not isinstance(invoice, dict) or not isinstance(selected_id, int):
            evidence = {
                "status": "INSUFFICIENT_EVIDENCE",
                "reason": "incomplete_invoice_observation",
                "fields": [],
            }
        else:
            try:
                persisted_result = parse_tool_result(await call_get_invoice(selected_id))
            except Exception:
                persisted_result = {}
            persisted = persisted_result.get("invoice")
            if persisted_result.get("success") is not True or not isinstance(persisted, dict):
                evidence = {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "reason": "persisted_invoice_not_found",
                    "invoice_id": selected_id,
                    "fields": [],
                }
            else:
                try:
                    search = json.loads(
                        await call_search_documents(
                            f"{persisted.get('invoice_number', '')} invoice"
                        )
                    )
                except (TypeError, json.JSONDecodeError):
                    search = []
                source = next(
                    (
                        item.get("source")
                        for item in search if isinstance(item, dict)
                        and isinstance(item.get("source"), str)
                        and item["source"].startswith("invoices/")
                    ),
                    None,
                )
                try:
                    document_result = (
                        parse_tool_result(await call_read_document(source))
                        if isinstance(source, str)
                        else {}
                    )
                except Exception:
                    document_result = {}
                content = document_result.get("content")
                if document_result.get("success") is not True or not isinstance(content, str):
                    evidence = {
                        "status": "INSUFFICIENT_EVIDENCE",
                        "reason": "authoritative_invoice_document_unavailable",
                        "invoice_id": selected_id,
                        "fields": [],
                    }
                else:
                    expected = {
                        "invoice_number": document_invoice_value(content, "Invoice Number"),
                        "amount": normalized_amount(document_invoice_value(content, "Total Amount Due")),
                        "due_date": normalized_document_date(
                            document_invoice_value(content, "Payment Due Date")
                        ),
                        "customer_name": document_invoice_value(content, "Customer Legal Name"),
                        "revision_status": document_invoice_value(content, "Revision Status"),
                    }
                    if any(value is None for value in expected.values()):
                        evidence = {
                            "status": "INSUFFICIENT_EVIDENCE",
                            "reason": "authoritative_invoice_document_incomplete",
                            "source": source,
                            "fields": [],
                        }
                    else:
                        actual = {
                            "invoice_number": persisted.get("invoice_number"),
                            "amount": normalized_amount(persisted.get("amount")),
                            "due_date": persisted.get("due_date"),
                            "customer_name": persisted.get("customer_name"),
                        }
                        checks = [
                            {
                                "field": field,
                                "expected": expected[field],
                                "actual": actual[field],
                                "matched": str(expected[field]).casefold()
                                == str(actual[field]).casefold(),
                            }
                            for field in actual
                        ]
                        revision_expected = expected["revision_status"]
                        revision_matches = (
                            persisted.get("id") == current.get("id")
                            and current.get("status") == "active"
                            and persisted.get("status") == "active"
                            and "active" in revision_expected.casefold()
                            and (
                                "supersedes" not in revision_expected.casefold()
                                or (
                                    isinstance(persisted.get("supersedes_invoice_number"), str)
                                    and persisted.get("revision_number", 0) > 0
                                )
                            )
                        )
                        checks.append(
                            {
                                "field": "current_revision",
                                "expected": revision_expected,
                                "actual": {
                                    "selected_invoice_id": persisted.get("id"),
                                    "current_invoice_id": current.get("id"),
                                    "status": persisted.get("status"),
                                    "revision_number": persisted.get("revision_number"),
                                    "supersedes_invoice_number": persisted.get("supersedes_invoice_number"),
                                },
                                "matched": revision_matches,
                            }
                        )
                        evidence = {
                            "status": "VERIFIED" if all(check["matched"] for check in checks) else "FAILED",
                            "source": source,
                            "invoice_id": selected_id,
                            "current_invoice_id": current.get("id"),
                            "fields": checks,
                        }
    stop_reason = (
        None
        if evidence["status"] == "VERIFIED"
        else "invoice_reconciliation_insufficient_evidence"
        if evidence["status"] == "INSUFFICIENT_EVIDENCE"
        else "invoice_reconciliation_failed"
    )
    return {
        "invoice_reconciliation_result": evidence,
        "execution_stop_reason": stop_reason,
    }


def format_reconciliation_value(value: Any) -> str:
    """Render structured reconciliation evidence without losing its value."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def invoice_reconciliation_final_response(
    evidence: dict[str, Any], execution_stop_reason: str | None
) -> str:
    """Produce the authoritative final report for a completed reconciliation."""
    status = evidence.get("status")
    if status == "VERIFIED":
        lines = [
            "Invoice reconciliation VERIFIED.",
            (
                "The selected active persisted invoice matched the available "
                "internal invoice document on the checked fields. This confirms "
                "only consistency between those internal sources."
            ),
        ]
    elif status == "FAILED":
        lines = [
            "Invoice reconciliation FAILED.",
            "One or more checked fields differ, so the invoice is not verified.",
        ]
    else:
        lines = [
            "Invoice reconciliation has INSUFFICIENT_EVIDENCE.",
            "The invoice is not verified because the required evidence was unavailable or incomplete.",
        ]

    for label in ("source", "invoice_id", "current_invoice_id", "reason"):
        if evidence.get(label) is not None:
            lines.append(f"{label.replace('_', ' ').capitalize()}: {format_reconciliation_value(evidence[label])}")

    fields = evidence.get("fields")
    if isinstance(fields, list) and fields:
        lines.append("Checked fields:")
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = str(field.get("field", "unknown field"))
            outcome = "PASS" if field.get("matched") is True else "FAIL"
            expected = format_reconciliation_value(field.get("expected"))
            actual = format_reconciliation_value(field.get("actual"))
            lines.append(f"- {name}: {outcome} (expected: {expected}; actual: {actual})")

    if execution_stop_reason:
        lines.append(f"Execution stop reason: {execution_stop_reason}")
    return "\n".join(lines)
