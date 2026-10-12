# Northstar design notes

This document holds the detail behind the [README](../README.md): how the system maps to the CentrAlign requirements, how each control works, what verification does and does not prove, and a configuration reference. Links point to the code and tests that implement each claim.

## Contents

1. [Mapping to the CentrAlign requirements](#1-mapping-to-the-centralign-requirements)
2. [Goal understanding, planning and tool selection](#2-goal-understanding-planning-and-tool-selection)
3. [Company knowledge and context](#3-company-knowledge-and-context)
4. [Architecture, MCP services and state](#4-architecture-mcp-services-and-state)
5. [Safety, failure handling and human input](#5-safety-failure-handling-and-human-input)
6. [Verification](#6-verification)
7. [Workflows](#7-workflows)
8. [Engineering decisions and trade-offs](#8-engineering-decisions-and-trade-offs)
9. [Configuration reference](#9-configuration-reference)
10. [Source map](#10-source-map)

## 1. Mapping to the CentrAlign requirements

This mapping uses the AI Engineering Intern problem statement: accept an outcome-oriented request, use tools, observe results, adapt safely, verify completion, ask for clarification or approval where needed, and return useful evidence. "Evidence" means implementation and tests; it is not a claim that every scenario has been exercised with a real model.

| CentrAlign capability | Northstar approach | Main implementation | Evidence | Boundary |
| --- | --- | --- | --- | --- |
| Goal understanding and autonomous execution | The LLM sees the goal plus accumulated messages and chooses the next tool or ends the run. | [`decide()`](../app/agent/graph.py), [`route_after_decision()`](../app/agent/graph.py) | [`test_agent_tool_selection.py`](../tests/test_agent_tool_selection.py) | Planning is model-driven, not a fixed plan generator. |
| Tool use and observation | Selected calls execute through local MCP servers; structured results enter the message state. | [`execute_tool()`](../app/agent/graph.py), [`mcp_client.py`](../app/agent/mcp_client.py) | Operations, invoice and communication tests | Tools are local stdio MCP services, not browser or desktop automation. |
| Company knowledge | A lexical retriever returns bounded Markdown chunks; onboarding writes require SOP and exact governing-contract evidence. | [`lexical.py`](../app/retrieval/lexical.py), [`retrieve_onboarding_knowledge()`](../app/agent/graph.py) | [`test_knowledge_retrieval.py`](../tests/test_knowledge_retrieval.py), [`test_onboarding_prerequisites.py`](../tests/test_onboarding_prerequisites.py) | Retrieval is lexical, not semantic. |
| Execution memory | `AgentState` carries messages, results, resolution evidence, retries, pending interactions and verification evidence. | [`AgentState`](../app/agent/state.py) | Graph tests | Checkpoints are in memory only. |
| Failure detection and recovery | Structured errors drive bounded transient retries; duplicate or repeated non-retryable actions are blocked. | [`execute_tool()`](../app/agent/graph.py), [`operations_server.py`](../mcp_servers/operations_server.py) | [`test_task11_transient_recovery.py`](../tests/test_task11_transient_recovery.py), [`test_task21_tool_errors.py`](../tests/test_task21_tool_errors.py) | Not every failure is safely recoverable. |
| Clarification and approval | Ambiguous identities and approval-required emails pause with `interrupt()` and resume on the same thread. | [`clarify()`](../app/agent/graph.py), [`approval()`](../app/agent/graph.py), [`main.py`](../app/main.py) | [`test_task13_clarification.py`](../tests/test_task13_clarification.py), [`test_task16_approval.py`](../tests/test_task16_approval.py) | Interrupts do not survive a process restart. |
| Safety controls | Deterministic gates enforce identity resolution, onboarding evidence, task inspection, project scope, email policy and smoke read-only mode. | [`graph.py`](../app/agent/graph.py), [`communication.py`](../app/policy/communication.py), [`database.py`](../app/database.py) | Prerequisite, policy, customer-resolution and completion tests | Policy is intentionally narrow, not general DLP or authorization. |
| Independent verification | Writes are read back through MCP and compared with expected fields; completeness review is separate. | [`verify_onboarding()`](../app/agent/graph.py), [`verify_invoice()`](../app/agent/graph.py), [`verify()`](../app/agent/graph.py) | Onboarding, communication and invoice verification tests | Write verification proves persisted fields, not business success. |
| Completion evidence | The final answer is constrained by verified IDs; diagnostics keep sanitized action and result evidence. | [`answer()`](../app/agent/graph.py), [`diagnostics.py`](../app/agent/diagnostics.py) | [`test_diagnostics.py`](../tests/test_diagnostics.py) | Model prose is a summary, not the source of truth. |

## 2. Goal understanding, planning and tool selection

[`app/main.py`](../app/main.py) creates an `AgentState` from the command-line goal and invokes [`graph`](../app/agent/graph.py). [`decide()`](../app/agent/graph.py) binds the LangChain tools to the configured OpenAI chat model and asks it for the next action. The model receives the goal and the prior tool conversation, so it can choose another lookup, a write, a clarification or a final response based on what it has already observed.

Northstar separates two things:

- **Model-driven choice:** which exposed tool to request next, and when the request is complete.
- **Deterministic runtime control:** whether that request is allowed, needs retrieval or identity resolution, needs approval, should retry, must be verified, or must stop.

The model provides flexible sequencing. It cannot bypass the runtime gates, and its final text is never treated as proof of completion.

## 3. Company knowledge and context

The Knowledge MCP server exposes `search_documents` and `read_document` over [`company_data/`](../company_data). [`lexical.py`](../app/retrieval/lexical.py) splits Markdown into paragraph-bounded chunks (at most 1,200 characters), normalizes tokens, scores token overlap, adds a filename-term boost, returns at most five results and keeps only the best chunk per source. This is lexical retrieval, not semantic or hybrid retrieval.

For onboarding, [`_onboarding_knowledge_gaps()`](../app/agent/graph.py) requires the onboarding SOP plus a retrieved contract chunk containing the resolved customer's exact legal name. If a proposed onboarding write lacks that evidence, [`retrieve_onboarding_knowledge()`](../app/agent/graph.py) runs a targeted search, and stops safely with `onboarding_knowledge_evidence_missing` if the evidence is still unavailable.

## 4. Architecture, MCP services and state

```mermaid
flowchart TD
    U[User goal / CLI / UI] --> M[main.py: AgentState + run_id]
    M --> G[LangGraph: decide]
    G <--> L[OpenAI chat model<br/>selects next exposed tool]
    G --> R{Deterministic routing<br/>and safety gates}
    R -->|knowledge needed| K[Knowledge MCP]
    K --> KB[company_data Markdown<br/>lexical retrieval]
    R -->|customer-scoped action| C[Customer resolution gate]
    C -->|ambiguous| H[LangGraph interrupt<br/>clarification]
    R -->|approved call| X[execute_tool]
    X --> MC[MCP client / stdio]
    MC --> O[Operations MCP]
    MC --> I[Invoice MCP]
    MC --> COM[Communication MCP]
    O --> DB[(PostgreSQL)]
    I --> DB
    COM --> DB
    COM -->|optional, opt-in| GM[Gmail OAuth adapter]
    X -->|approval required| A[LangGraph interrupt<br/>human approval]
    X -->|successful write| V{Read-back verifier}
    V -->|project/task| O
    V -->|invoice| I
    V -->|email| COM
    V --> G
    G -->|planning ends| Q[Onboarding completion review]
    Q -->|bounded missing-work repair| G
    Q --> F[Final response + optional<br/>sanitized diagnostics]
    A --> G
    H --> G
```

The graph nodes are `decide`, `retrieve_onboarding_knowledge`, `customer_resolution_gate`, `execute_tool`, `clarify`, `approval`, the verification nodes, `verify_requirements` and `answer`. Graph construction and the conditional edges are at the end of [`graph.py`](../app/agent/graph.py).

### MCP services

[`mcp_client.py`](../app/agent/mcp_client.py) launches each local FastMCP server over stdio and forwards the process environment.

| MCP server | Tools | Backing implementation |
| --- | --- | --- |
| [`knowledge_server.py`](../mcp_servers/knowledge_server.py) | `search_documents`, `read_document` | Local Markdown knowledge base |
| [`operations_server.py`](../mcp_servers/operations_server.py) | customer lookup and create; project and task list and create | PostgreSQL records, plus deterministic transient-failure injection |
| [`invoice_server.py`](../mcp_servers/invoice_server.py) | invoice list, get, log | PostgreSQL invoice records and revisions |
| [`communication_server.py`](../mcp_servers/communication_server.py) | `send_email`, `get_email` | PostgreSQL mock outbox by default; optional Gmail adapter |

### State

`AgentState` is working memory, not a durable business ledger. It stores message history, the latest tool batch, run-bound customer-resolution evidence, retries keyed by normalized action signature, pending human interactions, and verification and completion evidence. LangGraph's `InMemorySaver` checkpoints it while the process runs. [`ConfiguredGraph`](../app/agent/graph.py) uses the run ID as the default `thread_id`, so `Command(resume=...)` reaches the same checkpoint.

## 5. Safety, failure handling and human input

### Customer identity and write prerequisites

Before customer-scoped reads or writes, [`customer_resolution_gate()`](../app/agent/graph.py) resolves the user's reference through Operations MCP. Resolution evidence is tied to the current `run_id`; it cannot authorize another customer or another run. Ambiguous or missing results go to [`clarify()`](../app/agent/graph.py), which calls LangGraph `interrupt()` instead of picking a legal entity arbitrarily.

Customer lookup also accepts abbreviations. `Acme Corp` resolves to `Acme Corporation Inc.`, but a legal-form suffix the user types must match, so `Acme Corporation Ltd` does not resolve to the `Inc.` record. Once identity is resolved for a run, customer-scoped project and invoice calls are sent under the resolved legal name.

Task creation additionally requires a successful `list_tasks(project_id)` observation for the exact project. With `business_checks` enabled (the CLI and UI default), project and task actions must also be in observed, resolved-customer scope. These are deterministic preconditions, not model instructions.

### Structured errors, retries and limits

Tools return JSON such as `success`, `error_type` and `retryable`; retryability is never inferred from prose.

- `MAX_TOOL_STEPS = 20` bounds all tool executions in a run.
- `MAX_RETRIES_PER_ACTION = 3` allows an initial call plus three retries, only after an explicit retryable transient failure.
- Repeated non-retryable or duplicate actions are blocked by normalized action signature.
- Operations MCP can inject transient failures through `NORTHSTAR_TRANSIENT_FAILURES` and a state file, because stdio servers are short-lived subprocesses.
- Mixed tool batches are handled per write: successful `create_project` and `create_task` calls are read back even if sibling calls fail, and any call skipped by an early guard still gets an explicit `not_executed` result. See [`route_after_tool()`](../app/agent/graph.py) and [`test_onboarding_verification.py`](../tests/test_onboarding_verification.py).

This is bounded recovery, not a claim that every error is safely repairable.

### Communication policy and approval

[`evaluate_email()`](../app/policy/communication.py) returns a deterministic `allow`, `approval_required` or `deny` before `send_email` reaches Communication MCP. A verified billing or technical contact at the customer's known domain can be allowed; an unverified recipient requires approval; narrowly detected credential-like content is denied.

For approval-required work, the exact proposed arguments and reason are stored in `pending_approval_action`, then [`approval()`](../app/agent/graph.py) interrupts. Resuming with `approve` or `reject` re-checks policy before MCP execution. Rejected, denied or still-approval-requiring actions never reach the side-effect boundary.

## 6. Verification

A successful tool response is an observation, not proof. Northstar uses a different verification path for each outcome.

| Operation | Verification | On mismatch or missing evidence |
| --- | --- | --- |
| Project creation | [`_verify_created_project()`](../app/agent/graph.py) lists projects and compares ID, resolved customer ID, name and status. | `onboarding_verification_failed`; no automatic duplicate write. |
| Task creation | [`_verify_created_task()`](../app/agent/graph.py) reads the persisted project and task and compares ID, relationship, title and status. | Same safe stop; no corrective task write. |
| Invoice logging | [`verify_invoice()`](../app/agent/graph.py) gets the persisted invoice and compares customer, number, amount, dates, revision, status and supersession relation. | `invoice_verification_failed`. |
| Invoice reconciliation (read-only) | [`reconcile_invoice()`](../app/agent/graph.py) compares the selected active persisted invoice with the matching internal invoice Markdown document: amount, due date, customer name and active-revision evidence. | `VERIFIED`, `FAILED` or `INSUFFICIENT_EVIDENCE`; no write is attempted. |
| Mock email send | [`verify()`](../app/agent/graph.py) calls `get_email` and compares recipient, subject, body and status. | `communication_verification_failed`, or bounded repair for eligible observed mismatches. |

[`_verified_onboarding_claims()`](../app/agent/graph.py) limits final creation claims to IDs with successful read-back evidence. A project found by `list_projects` must be described as reused, not created.

### Onboarding completeness review

Write verification proves that an individual project or task was persisted as requested. It does not prove that the tasks meet all contract and SOP requirements. For that, when model planning stops for a resolved onboarding goal, [`verify_requirements()`](../app/agent/graph.py) independently:

1. reads the full governing contract and the reference documents (onboarding SOP, security policy) through Knowledge MCP;
2. reads persisted tasks only from customer projects this run created or inspected, through Operations MCP, so tasks left by earlier runs cannot satisfy a requirement; and
3. asks a separate LLM reviewer, given those documents and task records and not the agent's earlier reasoning, which required work items exist and which task covers each.

Missing items send a bounded repair instruction back to planning. `MAX_COMPLETION_REPAIRS = 2`; after that the run stops with `onboarding_requirements_incomplete` and reports the open work. A claimed task ID must exist in the read-back records, and its persisted title must contain requirement-specific, non-generic terms, so a generic setup task cannot be counted as covering unrelated requirements.

This is stronger than trusting the executing model, but it is not fully deterministic, because interpreting the requirements is an LLM judgment. If document retrieval or reviewer-output parsing fails, the result is `SKIPPED` (never a pass), and the UI reports such runs as unverified. See [`test_onboarding_completion.py`](../tests/test_onboarding_completion.py).

### What verification means

- Invoice `VERIFIED`: the selected active persisted invoice matched the source document on invoice number, amount, due date, customer name and current revision or status. `FAILED`: the evidence was available but one or more fields differed. `INSUFFICIENT_EVIDENCE`: selection, persistence, source-document or required-field evidence was unavailable. None of these establishes commercial or legal correctness.
- Onboarding write verification proves expected project and task fields were persisted and read back. It does not prove the business work behind a task was done.
- The completeness review checks document-derived requirements against persisted task titles. It does not prove that the underlying reviews, tests or approvals happened in the real world.

## 7. Workflows

### Customer onboarding

Example request: `Onboard Globex Corporation LLC`

1. The model requests customer information; the identity gate resolves the legal entity or interrupts for clarification.
2. Before onboarding writes, Northstar requires retrieved SOP and governing-contract evidence.
3. The model inspects existing projects and tasks, then may propose project and task writes.
4. The runtime requires exact-project task inspection and blocks out-of-scope projects.
5. Every successful write is read back independently. A passing verifier returns to planning, which supports multi-step onboarding.
6. When planning ends, the completeness review compares run-scoped persisted tasks with contract and SOP-derived work items, with up to two bounded repair cycles. Unresolved items are reported safely.

Tests cover no-duplicate behavior, multi-step onboarding, transient task recovery, prerequisites, customer resolution, read-back verification, completeness review and repair, scope checks and mixed-result batches. They do not show that every possible contract is interpreted correctly by the reviewer.

### Invoice inspection, logging and reconciliation

Example request: `Inspect the latest active invoice for Acme Corporation Inc.`

The model uses `list_invoices` to inspect revision relationships and `get_invoice` to inspect a selected record. [`database.py`](../app/database.py) seeds Acme with a superseded original invoice and an active final revision, `INV-2026-1018-R1`; the graph does not hardcode that selection. A read-only request routes to `reconcile_invoice`, which compares the selected persisted invoice with the internal invoice document. A log action is verified by `verify_invoice`. See [`test_task22_invoice_management.py`](../tests/test_task22_invoice_management.py) and [`test_invoice_reconciliation.py`](../tests/test_invoice_reconciliation.py).

### Controlled communication

Example request: `Send an onboarding update to Acme's verified technical contact.`

The runtime derives recipient context from customer records, applies policy, and then sends through mock communication, interrupts for approval, or stops. A successful mock write is verified through `get_email`. Mock email is the default and persists only to the PostgreSQL `emails` table. Gmail is an optional backend behind the same MCP interface; it is not required for development or tests and can cause real external effects if intentionally configured.

## 8. Engineering decisions and trade-offs

**Explicit graph routing instead of an unconstrained agent loop.**
*Problem:* a model can repeat failures, skip prerequisites or report success without evidence. *Approach:* LangGraph holds state and routes through named nodes and conditional edges; the model proposes actions and runtime code enforces identity, retrieval, policy, retry, verification and termination. *Trade-off:* the graph contains workflow-specific logic and is less flexible than a generic planner, but that makes it easier to test and defend.

**MCP boundaries for knowledge and operations.**
*Problem:* retrieval and side effects have different trust and testing needs. *Approach:* separate local FastMCP servers expose small stdio contracts, and the graph goes through one client layer instead of importing database code. *Trade-off:* subprocess transport is simple for a prototype but not built for throughput or distributed deployment.

**Retrieval as an evidence gate.**
*Problem:* a model might act on generic knowledge or skip the customer's contract. *Approach:* the runtime checks the chunks actually returned for both SOP and exact legal-entity contract evidence before onboarding writes. *Trade-off:* lexical matching can miss relevant language, so the completeness review reads whole documents rather than snippets.

**Read-back verification after side effects.**
*Problem:* a tool success or model statement may not equal persisted reality. *Approach:* project, task, invoice and email flows compare independently read state with expected fields; business completeness is a separate review. *Trade-off:* extra reads and specialized verifiers cost latency and code, but give concrete evidence and safer claims.

**Bounded recovery instead of blind retries.**
*Problem:* blind retries and repairs can duplicate business actions. *Approach:* only structured transient failures retry, and retry, email-repair and completion-repair limits are explicit. *Trade-off:* some recoverable situations stop instead of improvising, on purpose.

## 9. Configuration reference

### Environment variables

Copy [`.env.example`](../.env.example) to `.env` and replace only the placeholders. The app loads `.env` with `python-dotenv`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Real LLM runs | OpenAI credential; never commit it. |
| `OPENAI_MODEL` | Optional | Model name; defaults to `gpt-4o-mini`. |
| `DATABASE_URL` | Normal runs | PostgreSQL URL for the `northstar` database (required at import time). |
| `SMOKE_TEST_DATABASE_URL` | Smoke mode | URL for a database named `northstar_smoke`. |
| `COMMUNICATION_BACKEND` | Optional | `mock` by default; `gmail` is opt-in. |
| `NORTHSTAR_GMAIL_CREDENTIALS_PATH` / `NORTHSTAR_GMAIL_TOKEN_PATH` | Gmail only | OAuth client JSON and token cache paths. |
| `NORTHSTAR_GMAIL_TEST_RECIPIENT` / `NORTHSTAR_GMAIL_INTEGRATION_TESTS` | Optional | Recipient and opt-in switch (`1`) for live Gmail integration tests. |
| `NORTHSTAR_BUSINESS_CHECKS` | Optional | Defaults to `1`; set `0` only for narrow tests. |
| `NORTHSTAR_DIAGNOSTICS` | Optional | `1` emits sanitized observable-action diagnostics to stderr. |
| `NORTHSTAR_SMOKE_TEST_MODE` | Optional | Uses the isolated smoke database and forces mock communication. |
| `NORTHSTAR_SMOKE_TEST_READ_ONLY` | Optional | Blocks write-capable tools and database operations. |
| `NORTHSTAR_TRANSIENT_FAILURES` / `NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE` | Tests only | Example: `create_task:2`, with a state file path. |

### Database and demo data

PostgreSQL must be running, and the target database must already exist. The project has no migration system. This command creates the tables and seeds the demo customers and invoices:

```powershell
.venv\Scripts\python.exe -m app.database
```

It is meant for a fresh local demo database, not production migrations, and it never creates or resets the database server itself.

### Knowledge base and MCP servers

Knowledge documents are read from [`company_data/`](../company_data) by [`knowledge_server.py`](../mcp_servers/knowledge_server.py). The agent starts the four local stdio MCP servers on demand through [`mcp_client.py`](../app/agent/mcp_client.py); no separate MCP terminal is needed.

### Diagnostics and smoke mode

With `NORTHSTAR_DIAGNOSTICS=1`, one JSON report goes to stderr with the run ID, tool names, sanitized arguments and results, interrupts and resumes, stop reason, and verification and completion evidence. It omits bodies, document content, credentials, tokens and full email addresses, and it does not record model reasoning.

Smoke mode refuses to start unless `SMOKE_TEST_DATABASE_URL` names `northstar_smoke`, and it forces mock communication:

```powershell
$env:NORTHSTAR_SMOKE_TEST_MODE = "1"
$env:SMOKE_TEST_DATABASE_URL = "postgresql://user:password@localhost:5432/northstar_smoke"
$env:NORTHSTAR_DIAGNOSTICS = "1"
.venv\Scripts\python.exe -m app.main "Onboard Globex Corporation LLC"
```

Add `NORTHSTAR_SMOKE_TEST_READ_ONLY=1` for a no-write investigation. Do not run `app.database` in read-only mode.

### Transient-failure injection

For retry testing only:

```powershell
$env:NORTHSTAR_TRANSIENT_FAILURES = "create_task:2"
$env:NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE = "$env:TEMP\northstar-transient-state.json"
```

The first two matching Operations MCP calls return a structured retryable failure and the third executes normally. Clear the variables afterwards.

### HTTP API (demo UI)

The FastAPI adapter in [`api/main.py`](../api/main.py) exposes:

- `GET /api/health`
- `POST /api/runs` to start a run (`goal` and an idempotency `submission_id` are required)
- `GET /api/runs/{run_id}` for the sanitized run report
- `GET /api/runs/{run_id}/events` for replayable SSE events (optional `after` query parameter or `Last-Event-ID` header)
- `POST /api/runs/{run_id}/clarification` to resume a clarification interrupt
- `POST /api/runs/{run_id}/approval` with `approve` or `reject` to resume an approval interrupt

The adapter contains no agent logic. Run records, submission deduplication, checkpoints and event history are in memory and process-local, so run it as a single process without `--reload`.

### Running the tests

```powershell
$env:COMMUNICATION_BACKEND = "mock"
Remove-Item Env:NORTHSTAR_GMAIL_INTEGRATION_TESTS -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py" -v
```

Selecting the mock backend matters: a shell-level Gmail setting makes the mock-email tests expect the wrong result shape. Several tests start real MCP subprocesses and use the configured PostgreSQL database, so a full run takes a few minutes. Useful focused runs:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_onboarding_completion -v
.venv\Scripts\python.exe -m unittest tests.test_onboarding_verification -v
.venv\Scripts\python.exe -m unittest tests.test_onboarding_prerequisites -v
.venv\Scripts\python.exe -m unittest tests.test_task13_clarification -v
.venv\Scripts\python.exe -m unittest tests.test_invoice_reconciliation -v
.venv\Scripts\python.exe -m unittest tests.test_ui_api -v
```

The live Gmail integration test is skipped unless you opt in with `NORTHSTAR_GMAIL_INTEGRATION_TESTS=1`.

## 10. Source map

| Question | Start here |
| --- | --- |
| How does the graph decide and route? | [`app/agent/graph.py`](../app/agent/graph.py) |
| What state survives a node transition? | [`app/agent/state.py`](../app/agent/state.py) |
| How are MCP subprocesses called? | [`app/agent/mcp_client.py`](../app/agent/mcp_client.py) |
| How is retrieval scored? | [`app/retrieval/lexical.py`](../app/retrieval/lexical.py) |
| What is persisted and seeded? | [`app/database.py`](../app/database.py) |
| How are email policy decisions made? | [`app/policy/communication.py`](../app/policy/communication.py) |
| How are diagnostics sanitized? | [`app/agent/diagnostics.py`](../app/agent/diagnostics.py) |
| How does the UI talk to the graph? | [`api/main.py`](../api/main.py), [`ui/`](../ui) |
| What documents drive the demo? | [`company_data/`](../company_data) |
| What behavior is tested? | [`tests/`](../tests) |

## Security notes

`.env`, OAuth credentials and tokens, virtual environments, caches and logs are listed in [`.gitignore`](../.gitignore). Do not commit API keys, database passwords, Gmail files, or diagnostic output containing sensitive data. Diagnostics sanitize credentials, document bodies and full email addresses, but sanitization is not a guarantee that arbitrary logs are safe to publish.
