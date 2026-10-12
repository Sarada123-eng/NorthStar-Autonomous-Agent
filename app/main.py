import asyncio
import os
import sys
from uuid import uuid4

from langgraph.types import Command

from app.agent.graph import graph
from app.agent.diagnostics import emit_diagnostics
from app.agent.state import AgentState, RunStatus
from app.database import verify_database_target


async def main() -> None:
    if os.getenv("NORTHSTAR_SMOKE_TEST_MODE") == "1":
        # Smoke runs never use the Gmail backend, even if a parent shell set it.
        os.environ["COMMUNICATION_BACKEND"] = "mock"
    await verify_database_target()
    goal = " ".join(sys.argv[1:]).strip() or "Onboard Acme"
    initial_state: AgentState = {
        "run_id": str(uuid4()),
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
        "business_checks": os.getenv("NORTHSTAR_BUSINESS_CHECKS", "1") != "0",
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

    config = {"configurable": {"thread_id": initial_state["run_id"]}}
    transitions = []
    result = await graph.ainvoke(initial_state, config=config)
    while result.get("__interrupt__"):
        interrupt_value = result["__interrupt__"][0].value
        interrupt_type = interrupt_value.get("type")
        transitions.append({"event": "interrupt", "type": interrupt_type})
        if interrupt_type == "approval":
            print(interrupt_value["message"])
            print(f"Tool: {interrupt_value['tool']}")
            print(f"Arguments: {interrupt_value['arguments']}")
            prompt = "Approval (approve/reject): "
        elif interrupt_type == "clarification":
            print(interrupt_value["question"])
            prompt = "Clarification: "
        else:
            raise RuntimeError(
                f"Unsupported human-interaction interrupt type: {interrupt_type!r}"
            )
        result = await graph.ainvoke(
            Command(resume=input(prompt)),
            config=config,
        )
        transitions.append({"event": "resume", "type": interrupt_type})
    emit_diagnostics(result, transitions)
    print(result["messages"][-1].content)
    completion = result.get("onboarding_completion_result")
    if completion:
        print(
            f"\nCompletion check: {completion.get('status')} "
            f"(check {completion.get('attempt')})"
        )
        for item in completion.get("required_items", []):
            covered = item.get("satisfied_by_task_id")
            state_text = f"task {covered}" if covered else "NO MATCHING TASK"
            print(f"  - {item.get('item')}: {state_text}  [{item.get('basis')}]")
        if completion.get("reason"):
            print(f"  reason: {completion['reason']}")


if __name__ == "__main__":
    asyncio.run(main())
