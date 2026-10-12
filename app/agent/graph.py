import json
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
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

SYSTEM_PROMPT = """You are the Northstar assistant.

You help users interact with Northstar's internal knowledge and
operational systems.

Use internal tools when the user's request requires information or
actions involving Northstar's systems.

- Use `search_documents` when the answer depends on internal company
  policies, SOPs, contracts, invoices, or other company documents.
- Use `find_customer` when the answer depends on current customer
  records or operational status.
- Use `create_customer` when the user explicitly asks to create a
  customer and provides the required billing and technical contacts.
- For onboarding an existing or named customer, never use `create_customer`
  to request contacts. Before an onboarding write, consult the documented
  onboarding procedure and the resolved customer's governing contract with
  `search_documents`; then
  use `find_customer` and inspect onboarding state with `list_projects` and
  `list_tasks`.
- Use `list_projects` and `list_tasks` to inspect existing onboarding state
  before creating project or task records.
- `customer_id`, `project_id`, and `task_id` identify different entity types.
  Never use one as another. Obtain a `project_id` from a project result before
  calling `list_tasks` or `create_task`.
- `invoice_id` identifies a persisted invoice. Use `list_invoices` to inspect
  invoice revision relationships, select the active current revision rather
  than an older superseded invoice, and use `get_invoice` to inspect one ID.
- Use `log_invoice` only to create or update a real invoice record; never put
  invoice details into a generic project task title.
- Use `create_project` and `create_task` for internal onboarding planning
  actions supported by the discovered requirements.
- Before `create_task`, inspect the exact project's current tasks with a
  successful `list_tasks(project_id)` result. This is enforced at runtime.
- Use `request_clarification` when the available evidence leaves an important
  identity or requirement genuinely ambiguous. Ask one precise question and
  do not choose an entity arbitrarily.
- Use `send_email` when the user explicitly requests an email. It sends or
  records the email through the configured communication backend.
- Use `get_email` to inspect the resulting email record.
- Do not call an email a mock outbox record unless the tool result explicitly
  identifies the mock backend. Do not claim external delivery merely because
  `send_email` succeeded.
- Report verification accurately. A successful verification means the
  configured backend's resulting email record matched the intended email. If
  the tool result identifies Gmail/external sending and verification succeeds,
  you may say it was sent through Gmail and verified. If it identifies the
  mock backend, say it was recorded in the mock outbox. Never infer the
  backend from assumptions or prompt text.
- Communication actions are subject to a runtime policy gate; do not assume
  that proposing `send_email` means it can execute.
- Do not use internal tools for general knowledge or simple arithmetic.

When a tool is used, base your answer on the tool result.

A successful tool call is only one completed action or an observation; it
does not prove that the broader user goal is complete. After each result,
decide whether more safe work is required. Do not create duplicate
onboarding projects or tasks when the current state already shows them.

If a tool reports a failure, do not blindly retry the same action. If the
failure provides enough information for a different, safe corrective action,
you may replan and use an appropriate available tool. Otherwise, report the
failure clearly or ask the user for missing information.

If a tool result explicitly marks a failure as retryable and transient, retry
the same action when appropriate. The runtime enforces the retry limit.

When a customer creation reports a duplicate, investigate the existing
customer rather than retrying creation. First use `find_customer`, then use
`search_documents` to compare the available legal name, registration or tax
identifier, and domain information before deciding whether it can be reused.
Do not assume that a matching short name means the same legal entity. If the
identity is different or remains ambiguous, ask for clarification or explain
that the operation is blocked.

Do not invent missing information. If a required value for an operation
is missing, ask the user for it."""


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
    )


@tool
async def search_documents(query: str) -> str:
    """Search Northstar policies, SOPs, contracts, invoices, and internal documents.

    Use this for security requirements and all questions about documented
    company knowledge. Do not use it for current customer records or
    operational database state.
    """
    return await call_search_documents(query)


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
    return await call_find_customer(name)


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
    return await call_create_customer(name, billing_contact, technical_contact)


@tool
async def list_projects(customer_name: str) -> str:
    """List existing onboarding projects for a customer before creating one."""
    return await call_list_projects(customer_name)


@tool
async def create_project(customer_name: str, name: str) -> str:
    """Create an internal onboarding project for an existing customer."""
    return await call_create_project(customer_name, name)


@tool
async def list_tasks(project_id: int) -> str:
    """List existing tasks in an onboarding project before creating tasks."""
    return await call_list_tasks(project_id)


@tool
async def create_task(project_id: int, title: str) -> str:
    """Create one internal onboarding task in an existing project."""
    return await call_create_task(project_id, title)


@tool
async def list_invoices(customer_name: str) -> str:
    """List a customer's persisted invoices and their revision relationships."""
    return await call_list_invoices(customer_name)


@tool
async def get_invoice(invoice_id: int) -> str:
    """Retrieve one persisted invoice by invoice_id for inspection or verification."""
    return await call_get_invoice(invoice_id)


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
    return await call_log_invoice(
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
    return await call_send_email(to, subject, body)


@tool
async def get_email(email_id: int) -> str:
    """Retrieve the resulting email record from the configured backend."""
    return await call_get_email(email_id)


@tool
def request_clarification(question: str) -> str:
    """Ask the user for missing or ambiguous information before continuing."""
    return question


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
    if tool_name != "find_customer":
        return None
    explicit = result.get("resolution")
    if isinstance(explicit, dict) and explicit.get("status"):
        # Operations returns resolution metadata beside the canonical customer
        # record. Keep both pieces together so later customer-scoped gates can
        # validate the same identity without re-resolving it.
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


def _normalize_customer_reference(value: str) -> str:
    """Match the database's legal-name comparison normalization."""
    cleaned = re.sub(r"[.]+$", "", value.strip())
    return re.sub(r"\s+", " ", cleaned).casefold()


def _bind_customer_resolution(
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
    bound["normalized_reference"] = _normalize_customer_reference(
        requested_reference
    )
    # Preserve the original structured result for a later identical lookup.
    # It is used only after the run ID and normalized reference also match.
    bound["lookup_result"] = lookup_result
    return bound


def _customer_reference_for_current_actions(state: AgentState) -> str | None:
    """Return an explicit customer name requested by the current tool calls."""
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage):
        return _explicit_customer_reference(state["goal"])
    for tool_call in last_message.tool_calls:
        if tool_call["name"] not in CUSTOMER_SCOPED_ACTION_TOOLS:
            continue
        reference = tool_call["args"].get("customer_name")
        if isinstance(reference, str) and reference.strip():
            return reference
    return _explicit_customer_reference(state["goal"])


def _resolution_matches_reference(
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

    requested = _normalize_customer_reference(requested_reference)
    known_references = [
        resolution.get("normalized_reference"),
        resolution.get("reference"),
    ]
    customer = resolution.get("customer")
    if isinstance(customer, dict):
        known_references.extend(
            [customer.get("name"), customer.get("legal_name")]
        )
    return any(
        isinstance(reference, str)
        and _normalize_customer_reference(reference) == requested
        for reference in known_references
    )


def _cached_customer_lookup(
    state: AgentState, customer_name: str
) -> str | None:
    """Return same-run evidence for an identical resolved lookup, if any."""
    resolution = state.get("customer_resolution")
    if not _resolution_matches_reference(state, resolution, customer_name):
        return None
    lookup_result = resolution.get("lookup_result")
    if not isinstance(lookup_result, dict) or lookup_result.get("found") is not True:
        return None
    return json.dumps(lookup_result)


def _explicit_customer_reference(goal: str) -> str | None:
    """Extract a likely named customer only when the goal states one.

    Conservative extraction: stop at sentence boundaries ('. ' followed by
    an uppercase letter or end-of-string) and common clause-boundary words
    so that multi-sentence goals don't produce garbage customer names.
    """
    markers = (
        r"\bfor\s+(?:the\s+)?",
        r"\bonboard\s+",
        r"\bexact\s+legal\s+name\s+",
        r"\bcustomer\s+(?:named|called)\s+",
    )
    # Sentence / clause boundary: period followed by whitespace+uppercase,
    # or common imperative verbs that start a new instruction.
    _SENTENCE_BOUNDARY = re.compile(
        r"[.]\s+(?=[A-Z])"   # period-space-uppercase  (new sentence)
    )
    _CLAUSE_BOUNDARY = re.compile(
        r"\s+(?:verify|confirm|check|do\s+not|report|compare|ensure|retrieve"
        r"|inspect|and\s+(?:verify|confirm|check|report|do\s+not))\b",
        flags=re.IGNORECASE,
    )
    for marker in markers:
        matches = list(re.finditer(marker, goal, flags=re.IGNORECASE))
        if not matches:
            continue
        tail = goal[matches[-1].end():]
        # Truncate at the first sentence boundary
        sent_match = _SENTENCE_BOUNDARY.search(tail)
        if sent_match:
            tail = tail[:sent_match.start()]
        # Truncate at the first clause boundary
        clause_match = _CLAUSE_BOUNDARY.search(tail)
        if clause_match:
            tail = tail[:clause_match.start()]
        reference = tail.strip(" .,!?:;")
        reference = re.sub(
            r"\s+(?:project|customer|onboarding project)\s*$",
            "",
            reference,
            flags=re.IGNORECASE,
        ).strip(" .,!?:;")
        # A single short token such as "Acme" may be an alias shared by
        # multiple entities; let the normal find_customer/ambiguity path
        # establish that state instead of guessing from the goal alone.
        if (
            reference
            and " " in reference
            and re.match(r"[A-Z]", reference)
        ):
            return reference
    return None


def _resolution_question(state: AgentState) -> str:
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


def _blocked_customer_write(
    state: AgentState, tool_call: dict[str, Any]
) -> tuple[str, AIMessage]:
    question = _resolution_question(state)
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


def latest_customer_context(state: AgentState) -> dict[str, Any] | None:
    for message in reversed(state["messages"]):
        if not isinstance(message, ToolMessage):
            continue
        result = parse_tool_result(message.content)
        customer = result.get("customer")
        if isinstance(customer, dict):
            return customer
    return None


async def customer_context_for_recipient(
    state: AgentState, recipient: str
) -> dict[str, Any] | None:
    customer = latest_customer_context(state)
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


def _canonical_customer_args(
    state: AgentState, tool_call: dict[str, Any]
) -> dict[str, Any]:
    """Send customer-scoped calls under the resolved customer's legal name.

    Customer lookup accepts abbreviations such as "Acme Corp"; the project and
    invoice tools match names exactly. Once identity is resolved for this run,
    the canonical legal name is used instead of the model's wording.
    """
    if not state.get("business_checks") or tool_call["name"] not in {
        "list_projects",
        "create_project",
        "list_invoices",
        "log_invoice",
    }:
        return tool_call
    reference = tool_call["args"].get("customer_name")
    resolution = state.get("customer_resolution")
    customer = resolution.get("customer") if resolution else None
    legal_name = customer.get("legal_name") if isinstance(customer, dict) else None
    if (
        not isinstance(reference, str)
        or not legal_name
        or not _resolution_matches_reference(state, resolution, reference)
    ):
        return tool_call
    return {**tool_call, "args": {**tool_call["args"], "customer_name": legal_name}}


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


def _unexecuted_tool_messages(
    state: AgentState, answered: list[ToolMessage]
) -> list[ToolMessage]:
    """Answer tool calls skipped by an early stop so every tool_call has a result.

    The chat API rejects a conversation in which an assistant tool call has no
    matching tool message, which happens when a guard blocks one call of a
    parallel batch and the rest are never executed.
    """
    answered_ids = {message.tool_call_id for message in answered}
    skipped = json.dumps(
        {
            "success": False,
            "error_type": "not_executed",
            "retryable": False,
            "message": (
                "Not executed because an earlier tool call in the same step was "
                "blocked or stopped the run. Re-issue it if it is still needed."
            ),
        }
    )
    return [
        ToolMessage(content=skipped, tool_call_id=call["id"])
        for call in state["messages"][-1].tool_calls
        if call["id"] not in answered_ids
    ]


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

        action_signature = json.dumps(
            {"tool": tool_call["name"], "args": tool_call["args"]},
            sort_keys=True,
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
            tool_call["name"] in ONBOARDING_WRITE_TOOLS
            and result.get("tool") == tool_call["name"]
            and parse_tool_result(result.get("content", "")).get("success") is True
        ):
            writes.append((tool_call, result))
    return writes


def _resolved_customer_for_onboarding_verification(
    state: AgentState, customer_name: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Return run-bound customer evidence and an MCP lookup reference."""
    resolution = state.get("customer_resolution")
    if not _resolution_matches_reference(state, resolution, customer_name):
        return None, None
    customer = resolution.get("customer") if resolution else None
    if not isinstance(customer, dict) or not isinstance(customer.get("id"), int):
        return None, None
    reference = customer.get("legal_name") or customer.get("name")
    return (customer, reference) if isinstance(reference, str) and reference else (None, None)


def _observed_project_for_customer(
    state: AgentState, project_id: int, customer_id: int
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


async def _verify_created_project(
    state: AgentState, action: dict[str, Any], execution: dict[str, Any]
) -> dict[str, Any]:
    created = execution.get("project")
    customer, customer_reference = _resolved_customer_for_onboarding_verification(
        state, action["args"].get("customer_name")
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


async def _verify_created_task(
    state: AgentState, action: dict[str, Any], execution: dict[str, Any]
) -> dict[str, Any]:
    created = execution.get("task")
    customer, customer_reference = _resolved_customer_for_onboarding_verification(state)
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
    if not _observed_project_for_customer(state, project_id, customer["id"]):
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


async def verify_onboarding(state: AgentState) -> dict:
    """Read back successful onboarding writes without proposing corrective writes."""
    writes = _last_onboarding_writes(state)
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
                evidence = await _verify_created_project(state, action, execution)
            else:
                evidence = await _verify_created_task(state, action, execution)
            evidence_items.append(evidence)
            if evidence["status"] == "FAIL":
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


def _latest_email_action(state: AgentState) -> dict[str, Any] | None:
    for message in reversed(state["messages"]):
        if not isinstance(message, AIMessage):
            continue
        for tool_call in reversed(message.tool_calls):
            if tool_call["name"] == "send_email":
                return tool_call
    return None


async def verify(state: AgentState) -> dict:
    """Read the configured backend's email record and compare it with the intended action."""
    action = _latest_email_action(state)
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
    """Recognize the narrow, read-only invoice consistency workflow."""
    goal = state.get("goal", "").casefold()
    return "invoice" in goal and any(
        word in goal for word in ("verify", "verification", "reconcile", "reconciliation")
    )


def _should_run_invoice_reconciliation(state: AgentState) -> bool:
    return (
        _is_invoice_reconciliation_goal(state)
        and state.get("invoice_reconciliation_result") is None
        and not state.get("execution_stop_reason")
    )


def _latest_tool_observation(
    state: AgentState, tool_name: str
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


def _document_invoice_value(content: str, label: str) -> str | None:
    match = re.search(
        rf"\*\*{re.escape(label)}\*\*\s*\|\s*([^|\n]+)",
        content,
        re.IGNORECASE,
    )
    return match.group(1).strip().strip("*").strip() if match else None


def _normalized_amount(value: Any) -> str | None:
    try:
        return format(Decimal(re.sub(r"[^0-9.-]", "", str(value))), ".2f")
    except (InvalidOperation, ValueError):
        return None


def _normalized_document_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


async def reconcile_invoice(state: AgentState) -> dict:
    """Read-only consistency check of an active invoice and its source document.

    This is intentionally separate from ``verify_invoice``, which proves a
    log_invoice write persisted. Reconciliation compares the selected current
    database record to the available invoice Markdown source; it cannot prove
    commercial or legal correctness beyond those two internal sources.
    """
    listed, list_call = _latest_tool_observation(state, "list_invoices")
    selected, selected_call = _latest_tool_observation(state, "get_invoice")
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
                        "invoice_number": _document_invoice_value(content, "Invoice Number"),
                        "amount": _normalized_amount(_document_invoice_value(content, "Total Amount Due")),
                        "due_date": _normalized_document_date(
                            _document_invoice_value(content, "Payment Due Date")
                        ),
                        "customer_name": _document_invoice_value(content, "Customer Legal Name"),
                        "revision_status": _document_invoice_value(content, "Revision Status"),
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
                            "amount": _normalized_amount(persisted.get("amount")),
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
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return parsed


async def _completion_documents(state: AgentState, legal_name: str) -> dict[str, str]:
    """Read whole governing documents so the review is not limited to search snippets."""
    legal = " ".join(legal_name.casefold().split())
    candidates: list[str] = []

    def collect(chunks: Any) -> None:
        for chunk in chunks if isinstance(chunks, list) else []:
            source = chunk.get("source") if isinstance(chunk, dict) else None
            if (
                isinstance(source, str)
                and source.startswith("contracts/")
                and source not in candidates
            ):
                candidates.append(source)

    collect(_search_results(state))
    if not candidates:
        try:
            collect(json.loads(await call_search_documents(f"{legal_name} contract")))
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
            documents[source] = content[:MAX_COMPLETION_DOC_CHARS]
    if not documents:
        raise RuntimeError(f"governing contract for {legal_name} not found")
    for source in COMPLETION_REFERENCE_DOCS:
        payload = parse_tool_result(await call_read_document(source))
        if payload.get("success") is True and isinstance(payload.get("content"), str):
            documents[source] = payload["content"][:MAX_COMPLETION_DOC_CHARS]
    return documents


async def _completion_actual_tasks(
    legal_name: str, project_ids: set[int]
) -> list[dict[str, Any]]:
    """Read persisted tasks of the projects this run worked on, via Operations."""
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


COMPLETION_REVIEW_PROMPT = """You are an independent reviewer of customer onboarding completeness. You see only the customer's governing documents and the onboarding tasks that currently exist. You do not see how those tasks were created.

From the documents, list the internal work items Northstar staff must perform, review, approve or escalate for THIS customer's onboarding. Include:
- reviews, assessments, approvals and escalations that the SOP or policies make mandatory because of facts in this customer's contract (tier, seat or volume thresholds, regulated data, data residency, start or go-live timing, special or unusual commitments);
- mandatory items stated directly in the contract.
Exclude requirements that do not apply to this customer's facts, outbound customer emails, and anything that concerns other customers. If several agreements exist, use only the current executed one that names this legal entity and ignore superseded or expired ones.

For each item decide whether one existing task clearly covers it. A task covers an item only if its title names that work.

Respond with JSON only, no commentary:
{"required_items": [{"item": "short name", "basis": "document and clause requiring it, under 25 words", "satisfied_by_task_id": <existing task id or null>}]}"""


# These words describe generic project administration rather than evidence that
# a task covers a customer-specific requirement.  A title must still contain
# meaningful terms from the individual requirement before a reviewer claim can
# count as completion evidence.
_COMPLETION_GENERIC_TERMS = {
    "a",
    "an",
    "and",
    "assessment",
    "customer",
    "for",
    "initial",
    "mandatory",
    "onboarding",
    "review",
    "setup",
    "task",
    "the",
    "to",
    "verification",
    "work",
}


def _completion_terms(text: Any) -> set[str]:
    """Return non-generic title terms used to validate a coverage claim."""
    terms = set()
    for term in re.findall(r"[a-z0-9]+", str(text).casefold()):
        if term.endswith("s") and len(term) > 4:
            term = term[:-1]
        if term not in _COMPLETION_GENERIC_TERMS and len(term) > 2:
            terms.add(term)
    return terms


def _task_coverage_evidence(item: str, task: dict[str, Any]) -> dict[str, Any]:
    """Validate a reviewer claim against the actual persisted task title.

    The LLM identifies contract-derived requirements, but it cannot turn an
    existing generic task into evidence of every requirement.  For a title to
    satisfy an item, it must explicitly share the item's substantive terms.
    A one-term item (for example, security review) needs that one distinctive
    term; richer items need at least two.  This permits one explicitly scoped
    task to cover multiple items without treating a generic setup task as a
    universal match.
    """
    requirement_terms = _completion_terms(item)
    title = str(task.get("title", ""))
    title_terms = _completion_terms(title)
    matched_terms = sorted(requirement_terms & title_terms)
    required_matches = min(2, len(requirement_terms))
    covered = bool(requirement_terms) and len(matched_terms) >= required_matches
    return {
        "covered": covered,
        "task_title": title,
        "matched_terms": matched_terms,
        "required_terms": sorted(requirement_terms),
    }


async def _review_requirements(
    legal_name: str, documents: dict[str, str], tasks: list[dict[str, Any]]
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
    items = _json_object(str(response.content)).get("required_items")
    if not isinstance(items, list):
        raise ValueError("required_items missing from reviewer output")
    tasks_by_id = {
        task["task_id"]
        : task
        for task in tasks
        if isinstance(task.get("task_id"), int)
    }
    reviewed: list[dict[str, Any]] = []
    for item in items[:MAX_COMPLETION_ITEMS]:
        if not isinstance(item, dict) or not str(item.get("item", "")).strip():
            continue
        claimed = item.get("satisfied_by_task_id")
        requirement = str(item["item"]).strip()
        task = tasks_by_id.get(claimed) if isinstance(claimed, int) else None
        evidence = (
            _task_coverage_evidence(requirement, task)
            if task is not None
            else {
                "covered": False,
                "task_title": None,
                "matched_terms": [],
                "required_terms": sorted(_completion_terms(requirement)),
            }
        )
        reviewed.append(
            {
                "item": requirement,
                "basis": str(item.get("basis", ""))[:300],
                # A reviewer claim counts only when the persisted title also
                # provides requirement-specific coverage evidence.
                "satisfied_by_task_id": (
                    claimed if task is not None and evidence["covered"] else None
                ),
                "coverage_evidence": evidence,
            }
        )
    return reviewed


async def verify_requirements(state: AgentState) -> dict:
    """Compare persisted onboarding tasks with contract/SOP requirements.

    The reviewer reads the governing documents in full and the persisted
    tasks. It never sees the agent's own reasoning. Missing items send the run
    back to planning a bounded number of times, then the run stops and reports
    them instead of claiming completion.
    """
    attempt = state.get("completion_check_count", 0) + 1
    legal_name = state["customer_resolution"]["customer"]["legal_name"]
    try:
        documents = await _completion_documents(state, legal_name)
        tasks = await _completion_actual_tasks(
            legal_name,
            _run_project_ids(state, state["customer_resolution"]["customer"].get("id")),
        )
        reviewed = await _review_requirements(legal_name, documents, tasks)
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
    if attempt <= MAX_COMPLETION_REPAIRS:
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
                f"with no matching task after {MAX_COMPLETION_REPAIRS} repair "
                f"attempts: {listed}. Report the onboarding status accurately: say "
                "what was done, and list these as open items that need human action. "
                "Do not claim onboarding is complete."
            )
        )
    ]
    return update


def route_after_completion_check(state: AgentState) -> str:
    result = state.get("onboarding_completion_result") or {}
    if result.get("status") != "FAIL":
        return END
    return "answer" if state.get("execution_stop_reason") else "decide"


def _format_reconciliation_value(value: Any) -> str:
    """Render structured reconciliation evidence without losing its value."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _invoice_reconciliation_final_response(
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
            lines.append(f"{label.replace('_', ' ').capitalize()}: {_format_reconciliation_value(evidence[label])}")

    fields = evidence.get("fields")
    if isinstance(fields, list) and fields:
        lines.append("Checked fields:")
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = str(field.get("field", "unknown field"))
            outcome = "PASS" if field.get("matched") is True else "FAIL"
            expected = _format_reconciliation_value(field.get("expected"))
            actual = _format_reconciliation_value(field.get("actual"))
            lines.append(f"- {name}: {outcome} (expected: {expected}; actual: {actual})")

    if execution_stop_reason:
        lines.append(f"Execution stop reason: {execution_stop_reason}")
    return "\n".join(lines)


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
