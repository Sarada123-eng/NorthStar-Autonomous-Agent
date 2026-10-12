import json
from typing import Any

from langchain_core.messages import ToolMessage

from app.agent.state import AgentState


def action_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    """Return the stable signature used for repeated-action detection."""
    return json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True)


def unexecuted_tool_messages(
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
