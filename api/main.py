"""Single-process FastAPI adapter over the existing Northstar graph.

The adapter owns only HTTP lifecycle, presentation-safe events, and in-memory
run metadata. Graph execution, policy, retries, business operations, and
verification remain in app.agent.graph and its MCP-backed tools.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agent.diagnostics import _sanitize, _tool_result
from app.agent.graph import graph
from app.agent.state import RunStatus


PROJECT_ROOT = Path(__file__).parents[1]
UI_ROOT = PROJECT_ROOT / "ui"
TERMINAL_STATUSES = {"completed", "failed"}


class StartRunRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=4_000)
    submission_id: str = Field(min_length=8, max_length=128)


class ClarificationRequest(BaseModel):
    response: str = Field(min_length=1, max_length=2_000)


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]


class RunRecord:
    def __init__(self, run_id: str, goal: str, submission_id: str) -> None:
        self.run_id = run_id
        self.goal = goal
        self.submission_id = submission_id
        self.status = "pending"
        self.events: list[dict[str, Any]] = []
        self.state: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self.error: str | None = None
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self.evidence_sources: set[str] = set()
        self.retryable_actions: set[str] = set()
        self.lock = asyncio.Lock()
        self.event_signal = asyncio.Event()


runs: dict[str, RunRecord] = {}
submissions: dict[str, str] = {}

app = FastAPI(title="Northstar Demo API", version="0.1.0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _initial_state(goal: str, run_id: str) -> dict[str, Any]:
    """Mirror the CLI's initial graph input; execution remains graph-owned."""
    return {
        "run_id": run_id,
        "goal": goal,
        "messages": [],
        "status": RunStatus.RUNNING,
        "plan": [],
        "current_action": None,
        "retrieved_context": [],
        "last_tool_result": None,
        "last_tool_succeeded": None,
        "pending_human_input": None,
        "pending_human_input_type": None,
        "human_response": None,
        "pending_approval_action": None,
        "customer_resolution": None,
        "verification_result": None,
        "onboarding_verification_result": None,
        "invoice_verification_result": None,
        "invoice_reconciliation_result": None,
        "verification_evidence": None,
        "verification_repair_count": 0,
        "business_checks": True,
        "completion_check_count": 0,
        "onboarding_completion_result": None,
        "tool_step_count": 0,
        "retry_counts": {},
        "last_action_signature": None,
        "consecutive_action_count": 0,
        "last_failed_non_retryable_action": None,
        "execution_stop_reason": None,
        "error": None,
    }


def _event(record: RunRecord, event_type: str, payload: dict[str, Any]) -> None:
    record.events.append(
        {
            "sequence": len(record.events) + 1,
            "timestamp": _now(),
            "type": event_type,
            "payload": _sanitize(payload),
        }
    )
    record.event_signal.set()


def _interrupt_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, (tuple, list)) and value:
        value = value[0]
    value = getattr(value, "value", value)
    return value if isinstance(value, dict) and value.get("type") else None


def _pending_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    pending_action = state.get("pending_approval_action")
    if isinstance(pending_action, dict):
        return {
            "type": "approval",
            "tool": pending_action.get("tool"),
            "arguments": pending_action.get("args"),
            "reason": pending_action.get("reason"),
            "message": "Approval is required for this exact communication.",
        }
    # Tool-call messages are durable conversation history.  A completed run
    # can therefore retain the request_clarification call that caused an
    # earlier interrupt.  Only the explicit pending fields describe whether
    # the graph is currently waiting for a fresh answer.
    if state.get("pending_human_input_type") == "clarification":
        question = state.get("pending_human_input")
        if isinstance(question, str) and question.strip():
            return {
                "type": "clarification",
                "question": question,
                "goal": state.get("goal"),
            }
    return None


def _report(record: RunRecord) -> dict[str, Any]:
    state = record.state or {}
    final_message = None
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage) and not message.tool_calls:
            final_message = str(message.content)
            break
    verification = {
        "communication": state.get("verification_result"),
        "onboarding_writes": state.get("onboarding_verification_result"),
        "invoice": state.get("invoice_verification_result"),
        "invoice_reconciliation": state.get("invoice_reconciliation_result"),
        "onboarding_completion": state.get("onboarding_completion_result"),
    }
    verified = any(
        isinstance(value, dict) and value.get("status") in {"PASS", "VERIFIED"}
        for value in verification.values()
    )
    completion = verification["onboarding_completion"]
    if isinstance(completion, dict) and completion.get("status") != "PASS":
        # Written records alone do not prove the onboarding requirements were met.
        verified = False
    reconciliation = verification["invoice_reconciliation"]
    if isinstance(reconciliation, dict) and reconciliation.get("status") != "VERIFIED":
        verified = False
    if record.status == "completed" and verified:
        outcome = "verified"
    elif record.status == "completed":
        outcome = "unverified"
    elif record.status.startswith("awaiting_"):
        outcome = "awaiting_human_input"
    else:
        outcome = "blocked_or_failed"
    return _sanitize(
        {
            "run_id": record.run_id,
            "goal": record.goal,
            "status": record.status,
            "outcome": outcome,
            "summary": final_message,
            "execution_stop_reason": state.get("execution_stop_reason"),
            "error": record.error,
            "verification": verification,
            "pending": record.pending,
        }
    )


def _action_signature(tool: str, arguments: Any) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True, default=str)


def _retrieved_evidence(tool: str, content: str) -> list[dict[str, Any]]:
    """Expose only observable retrieval output as UI evidence."""
    if tool not in {"search_documents", "read_document"}:
        return []
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return []

    items = payload if tool == "search_documents" and isinstance(payload, list) else [payload]
    evidence = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            continue
        excerpt = item.get("content")
        if not isinstance(excerpt, str) or not excerpt.strip():
            continue
        evidence.append(
            {
                "source": item["source"],
                "score": item.get("score"),
                "excerpt": excerpt,
            }
        )
    return evidence


def _record_messages(record: RunRecord, update: dict[str, Any]) -> None:
    for message in update.get("messages", []) if isinstance(update, dict) else []:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                record.tool_calls[call["id"]] = {
                    "tool": call["name"],
                    "arguments": call.get("args", {}),
                }
                signature = _action_signature(call["name"], call.get("args", {}))
                if signature in record.retryable_actions:
                    _event(record, "retry_attempt", {"tool": call["name"], "tool_call_id": call["id"]})
                _event(
                    record,
                    "tool_selected",
                    {"tool": call["name"], "tool_call_id": call["id"], "arguments": call.get("args", {})},
                )
        elif isinstance(message, ToolMessage):
            result = _tool_result(message.content)
            call = record.tool_calls.get(message.tool_call_id, {})
            tool = str(call.get("tool", "unknown"))
            evidence = _retrieved_evidence(tool, message.content)
            if evidence:
                evidence = [
                    item
                    for item in evidence
                    if item["source"] not in record.evidence_sources
                ]
                record.evidence_sources.update(item["source"] for item in evidence)
            if evidence:
                _event(
                    record,
                    "evidence",
                    {
                        "tool": tool,
                        "tool_call_id": message.tool_call_id,
                        "sources": evidence,
                    },
                )
            if isinstance(result, dict) and result.get("success") is False:
                event_type = "tool_failure"
                if result.get("retryable") is True:
                    event_type = "retryable_failure"
                    record.retryable_actions.add(_action_signature(tool, call.get("arguments", {})))
            else:
                event_type = "tool_result"
            _event(record, event_type, {"tool": tool, "tool_call_id": message.tool_call_id, "result": result})


def _record_verification_evidence(
    record: RunRecord, sources: list[dict[str, Any]]
) -> None:
    fresh = []
    for item in sources:
        source = item.get("source")
        excerpt = item.get("excerpt")
        if (
            isinstance(source, str)
            and source not in record.evidence_sources
            and isinstance(excerpt, str)
            and excerpt.strip()
        ):
            record.evidence_sources.add(source)
            fresh.append({"source": source, "excerpt": excerpt})
    if fresh:
        _event(
            record,
            "evidence",
            {
                "tool": "read_document",
                "tool_call_id": "verification-retrieval",
                "sources": fresh,
            },
        )


def _record_update(record: RunRecord, update: dict[str, Any]) -> None:
    interrupt = _interrupt_payload(update.get("__interrupt__"))
    if interrupt:
        record.pending = interrupt
        record.status = f"awaiting_{interrupt['type']}"
        _event(record, "human_input_required", interrupt)
        return
    for node, payload in update.items():
        if node == "__interrupt__":
            continue
        _event(record, "node_update", {"node": node})
        if isinstance(payload, dict):
            _record_messages(record, payload)
            if isinstance(payload.get("verification_evidence"), list):
                _record_verification_evidence(
                    record, payload["verification_evidence"]
                )
            for key in (
                "verification_result",
                "onboarding_verification_result",
                "invoice_verification_result",
                "invoice_reconciliation_result",
                "onboarding_completion_result",
            ):
                if payload.get(key) is not None:
                    _event(record, "verification", {"kind": key, "evidence": payload[key]})
            if payload.get("execution_stop_reason"):
                _event(record, "execution_stopped", {"reason": payload["execution_stop_reason"]})


async def _drive(record: RunRecord, graph_input: Any) -> None:
    """Drive the existing graph once and publish only its observable updates."""
    async with record.lock:
        record.status = "running"
        record.pending = None
        _event(record, "run_resumed" if isinstance(graph_input, Command) else "run_started", {"run_id": record.run_id, "goal": record.goal})
        config = {"configurable": {"thread_id": record.run_id}}
        try:
            async for update in graph.astream(graph_input, config=config, stream_mode="updates"):
                _record_update(record, update)
            snapshot = await graph.aget_state(config)
            record.state = dict(snapshot.values)
            if record.pending is None:
                record.pending = _pending_from_state(record.state)
            if record.pending:
                record.status = f"awaiting_{record.pending['type']}"
            elif record.state.get("execution_stop_reason"):
                record.status = "failed"
            else:
                record.status = "completed"
            _event(record, "run_status", {"status": record.status, "stop_reason": record.state.get("execution_stop_reason")})
        except Exception as error:  # Surface adapter/graph failures, never convert them to success.
            record.status = "failed"
            record.error = f"{type(error).__name__}: {error}"[:500]
            _event(record, "run_error", {"error": record.error})


def _get_run(run_id: str) -> RunRecord:
    record = runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return record


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/runs", status_code=202)
async def start_run(request: StartRunRequest) -> dict[str, Any]:
    goal = request.goal.strip()
    if len(goal) < 3:
        raise HTTPException(status_code=422, detail="Goal must contain at least three characters.")
    existing_id = submissions.get(request.submission_id)
    if existing_id:
        return {"run_id": existing_id, "reused_submission": True, "status": runs[existing_id].status}
    run_id = str(uuid4())
    record = RunRecord(run_id, goal, request.submission_id)
    runs[run_id] = record
    submissions[request.submission_id] = run_id
    asyncio.create_task(_drive(record, _initial_state(goal, run_id)))
    return {"run_id": run_id, "reused_submission": False, "status": "pending"}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    return _report(_get_run(run_id))


@app.get("/api/runs/{run_id}/events")
async def events(
    run_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    record = _get_run(run_id)
    try:
        last_seen = max(after, int(last_event_id or 0))
    except ValueError:
        last_seen = after

    async def stream():
        index = max(0, last_seen)
        while True:
            while index < len(record.events):
                event = record.events[index]
                index += 1
                yield f"id: {event['sequence']}\nevent: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
            if record.status in TERMINAL_STATUSES:
                return
            try:
                await asyncio.wait_for(record.event_signal.wait(), timeout=15)
                record.event_signal.clear()
            except TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def _resume(record: RunRecord, response: str, expected_type: str) -> dict[str, Any]:
    if record.status != f"awaiting_{expected_type}":
        raise HTTPException(status_code=409, detail=f"Run is not awaiting {expected_type} input.")
    # Set status before scheduling to make repeated browser submissions conflict.
    record.status = "resuming"
    asyncio.create_task(_drive(record, Command(resume=response)))
    return {"run_id": record.run_id, "status": "resuming"}


@app.post("/api/runs/{run_id}/clarification", status_code=202)
async def submit_clarification(run_id: str, request: ClarificationRequest) -> dict[str, Any]:
    return await _resume(_get_run(run_id), request.response.strip(), "clarification")


@app.post("/api/runs/{run_id}/approval", status_code=202)
async def submit_approval(run_id: str, request: ApprovalRequest) -> dict[str, Any]:
    return await _resume(_get_run(run_id), request.decision, "approval")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(UI_ROOT / "index.html")


app.mount("/ui", StaticFiles(directory=UI_ROOT), name="ui")
