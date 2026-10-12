"""Minimal working memory passed between LangGraph nodes."""

from enum import Enum
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class RunStatus(str, Enum):
    """The execution states used for routing and pause/resume."""

    RUNNING = "running"
    WAITING_FOR_CLARIFICATION = "waiting_for_clarification"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class HumanInputType(str, Enum):
    """Distinguishes a question from an approval gate when resuming."""

    CLARIFICATION = "clarification"
    APPROVAL = "approval"


class AgentState(TypedDict):
    """Working memory for one generic autonomous execution.

    Fields are intentionally summaries or identifiers. Full documents, domain
    records, tool schemas, and persistence details belong outside graph state.
    LangGraph nodes may return partial updates to this schema.
    """

    run_id: str
    goal: str
    messages: Annotated[
        list[BaseMessage],
        "Conversation history used by planning and execution nodes.",
        add_messages,
    ]
    status: RunStatus

    # A compact ordered description is enough for execution and replanning.
    plan: list[str]
    current_action: Optional[str]

    # Store references or short summaries, never entire knowledge documents.
    retrieved_context: list[str]

    # The latest observation lets routing decide whether to continue, retry,
    # replan, or verify without retaining an unbounded tool transcript.
    last_tool_result: Optional[dict]
    last_tool_succeeded: Optional[bool]

    # These capture clarification or approval interaction data. LangGraph
    # checkpoints preserve them across an interrupt and same-thread resume;
    # a consuming node may clear them when that becomes appropriate.
    pending_human_input: Optional[str]
    pending_human_input_type: Optional[HumanInputType]
    human_response: Optional[str]
    pending_approval_action: Optional[dict]
    # Structured customer identity evidence required before customer writes.
    customer_resolution: Optional[dict]

    verification_result: Optional[dict]
    onboarding_verification_result: Optional[dict]
    invoice_verification_result: Optional[dict]
    # Read-only comparison of a selected persisted invoice against an
    # authoritative invoice document. This is distinct from log-write
    # verification above.
    invoice_reconciliation_result: Optional[dict]
    verification_repair_count: int
    # Opt-in switch for the stricter business-level checks (independent
    # onboarding completion review and project-scope guards). Callers such as
    # the CLI or an API set it; unit tests of the base loop leave it unset.
    business_checks: bool
    completion_check_count: int
    onboarding_completion_result: Optional[dict]
    # Actual documents read by independent completion verification, exposed
    # separately from the verification verdict for observability.
    verification_evidence: Optional[list[dict]]
    # Total tool executions for the current run, independent of retries.
    tool_step_count: int
    # Counts bounded retries for each normalized action signature after an
    # explicit retryable transient failure.
    retry_counts: dict[str, int]
    last_action_signature: Optional[str]
    consecutive_action_count: int
    last_failed_non_retryable_action: Optional[str]
    execution_stop_reason: Optional[str]
    error: Optional[str]
