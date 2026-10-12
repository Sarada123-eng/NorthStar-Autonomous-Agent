# Northstar Autonomy Engine

Northstar is a prototype autonomous task worker for internal company operations. A user supplies a natural-language goal; an LLM selects from a small, explicit tool set; LangGraph routes the resulting observations through deterministic safety, approval, retry, and verification controls. The prototype makes the difference between a plausible model response and evidence of a persisted outcome visible.

Built as an AI Engineering Intern submission for CentrAlign AI, its scope is deliberately narrow: company-document retrieval, customer onboarding, invoices, and controlled communications. It is not a browser agent, desktop-control system, or general-purpose enterprise automation platform.

## What it demonstrates

- Dynamic LLM tool selection inside an explicit LangGraph execution loop.
- Local MCP servers that separate knowledge retrieval, operations, invoices, and communications.
- PostgreSQL-backed customer, project, task, invoice, and mock-email records.
- Lexical Markdown retrieval over contracts, SOPs, policies, and invoices.
- Customer identity resolution, native LangGraph clarification interrupts, and approval interrupts.
- Structured failures, bounded transient retries, duplicate/repeated-action guards, and a 20-tool-step budget.
- Independent read-back verification for persisted onboarding writes, invoice logs, and communication records.
- Opt-in diagnostics that report observable actions while redacting sensitive fields and message/document content.

## How Northstar addresses the CentrAlign requirements

This mapping uses the supplied AI Engineering Intern problem statement: accept an outcome-oriented request, use tools, observe results, adapt safely, verify completion, ask for clarification or approval where needed, and return useful evidence. Evidence means implementation and/or tests; it is not a claim that every scenario has been exercised with a real model.

| CentrAlign capability | Northstar approach | Main implementation | Evidence | Important boundary |
| --- | --- | --- | --- | --- |
| Goal understanding and autonomous execution | The LLM sees the goal plus accumulated messages and chooses the next tool or ends the run. | [`decide()`](app/agent/graph.py), [`route_after_decision()`](app/agent/graph.py) | [`test_agent_tool_selection.py`](tests/test_agent_tool_selection.py) | Planning is model-driven, not a fixed plan generator. |
| Tool use and observation | Selected calls execute through local MCP; structured results enter the message state. | [`execute_tool()`](app/agent/graph.py), [`mcp_client.py`](app/agent/mcp_client.py) | Operations, invoice, and communication tests | Tools are local stdio MCP services, not browser/desktop automation. |
| Company knowledge | A lexical retriever returns bounded Markdown chunks; onboarding writes require SOP and exact governing-contract evidence. | [`lexical.py`](app/retrieval/lexical.py), [`retrieve_onboarding_knowledge()`](app/agent/graph.py) | [`test_knowledge_retrieval.py`](tests/test_knowledge_retrieval.py), [`test_onboarding_prerequisites.py`](tests/test_onboarding_prerequisites.py) | Retrieval is lexical, not embedding/vector/semantic search. |
| Execution memory | `AgentState` carries messages, results, resolution evidence, retries, pending interaction, and verification evidence. | [`AgentState`](app/agent/state.py) | Graph tests | Checkpoints are in memory only; they do not survive restart. |
| Failure detection and recovery | Structured errors drive bounded transient retries; duplicate/repeated non-retryable actions are blocked. | [`execute_tool()`](app/agent/graph.py), [`operations_server.py`](mcp_servers/operations_server.py) | [`test_task11_transient_recovery.py`](tests/test_task11_transient_recovery.py), [`test_task21_tool_errors.py`](tests/test_task21_tool_errors.py) | Not every failure is safely recoverable. |
| Clarification and approval | Ambiguous identities and approval-required emails pause with `interrupt()` and resume on the same thread. | [`clarify()`](app/agent/graph.py), [`approval()`](app/agent/graph.py), [`main.py`](app/main.py) | [`test_task13_clarification.py`](tests/test_task13_clarification.py), [`test_task16_approval.py`](tests/test_task16_approval.py) | The CLI is interactive; persistence is not durable. |
| Safety controls | Deterministic gates enforce identity resolution, onboarding evidence, task inspection, project scope, email policy, and smoke read-only mode. | [`graph.py`](app/agent/graph.py), [`communication.py`](app/policy/communication.py), [`database.py`](app/database.py) | Prerequisite, policy, customer-resolution, and completion tests | Policy is intentionally narrow, not general DLP/authorization. |
| Independent verification | Writes are read back via MCP/database reads and compared with expected fields; completion review is separate. | [`verify_onboarding()`](app/agent/graph.py), [`verify_invoice()`](app/agent/graph.py), [`verify()`](app/agent/graph.py) | Onboarding, communication, and invoice verification tests | Write verification proves persisted fields, not complete business success. |
| Completion evidence | Final-answer prompting is constrained by verified IDs; diagnostics retain sanitized observable action/result evidence. | [`answer()`](app/agent/graph.py), [`diagnostics.py`](app/agent/diagnostics.py) | [`test_diagnostics.py`](tests/test_diagnostics.py) | Model prose remains a summary, not the source of truth. |

### Goal understanding, planning, and tool selection

[`app/main.py`](app/main.py) creates an `AgentState` from the command-line goal and invokes [`graph`](app/agent/graph.py). [`decide()`](app/agent/graph.py) binds the available LangChain tools to the configured OpenAI chat model and asks the model for the next observable action. It receives the goal and prior tool conversation, so it can choose another lookup, write, clarification, or final response based on results already observed.

Northstar deliberately separates:

- **Model-driven choice:** which exposed tool to request next and when the model considers the request complete.
- **Deterministic runtime control:** whether that request is allowed, needs retrieval or identity resolution, needs approval, should retry, must verify, or must stop.

The LLM provides flexible sequencing; it cannot bypass runtime gates or use its final text as proof of completion.

### Company knowledge and context

The Knowledge MCP server exposes `search_documents` and `read_document` over [`company_data/`](company_data). [`app/retrieval/lexical.py`](app/retrieval/lexical.py) splits Markdown into paragraph-bounded chunks (maximum 1,200 characters), normalizes tokens, scores token overlap, adds a filename-term boost, returns at most five results, and keeps only the best chunk per source. This is **lexical retrieval**, not semantic or hybrid retrieval.

For onboarding, [`_onboarding_knowledge_gaps()`](app/agent/graph.py) requires the onboarding SOP plus a retrieved contract chunk containing the resolved customer's exact legal name. If a proposed onboarding write lacks that evidence, [`retrieve_onboarding_knowledge()`](app/agent/graph.py) performs a targeted search and safely stops with `onboarding_knowledge_evidence_missing` if it remains unavailable.

## Architecture

```mermaid
flowchart TD
    U[User goal / CLI] --> M[main.py: AgentState + run_id]
    M --> G[LangGraph: decide]
    G <--> L[OpenAI chat model\nselects next exposed tool]
    G --> R{Deterministic routing\nand safety gates}
    R -->|knowledge needed| K[Knowledge MCP]
    K --> KB[company_data Markdown\nlexical retrieval]
    R -->|customer-scoped action| C[Customer resolution gate]
    C -->|ambiguous| H[LangGraph interrupt\nclarification]
    R -->|approved call| X[execute_tool]
    X --> MC[MCP client / stdio]
    MC --> O[Operations MCP]
    MC --> I[Invoice MCP]
    MC --> COM[Communication MCP]
    O --> DB[(PostgreSQL)]
    I --> DB
    COM --> DB
    COM -->|optional, opt-in| GM[Gmail OAuth adapter]
    X -->|approval required| A[LangGraph interrupt\nhuman approval]
    X -->|successful write| V{Read-back verifier}
    V -->|project/task| O
    V -->|invoice| I
    V -->|email| COM
    V --> G
    G -->|planning ends| Q[Onboarding completion review]
    Q -->|bounded missing-work repair| G
    Q --> F[Final response + optional\nsanitized diagnostics]
    A --> G
    H --> G
```

The graph nodes are [`decide`](app/agent/graph.py), [`retrieve_onboarding_knowledge`](app/agent/graph.py), [`customer_resolution_gate`](app/agent/graph.py), [`execute_tool`](app/agent/graph.py), [`clarify`](app/agent/graph.py), [`approval`](app/agent/graph.py), the verification nodes, [`verify_requirements`](app/agent/graph.py), and [`answer`](app/agent/graph.py). Graph construction and conditional edges are at the end of [`graph.py`](app/agent/graph.py).

### MCP services and state

[`app/agent/mcp_client.py`](app/agent/mcp_client.py) launches each local FastMCP server over stdio and forwards the process environment.

| MCP server | Tools | Backing implementation |
| --- | --- | --- |
| [`knowledge_server.py`](mcp_servers/knowledge_server.py) | `search_documents`, `read_document` | Local Markdown knowledge base |
| [`operations_server.py`](mcp_servers/operations_server.py) | customer lookup/create; project/task list/create | PostgreSQL records and deterministic transient-failure injection |
| [`invoice_server.py`](mcp_servers/invoice_server.py) | invoice list/get/log | PostgreSQL invoice records and revisions |
| [`communication_server.py`](mcp_servers/communication_server.py) | `send_email`, `get_email` | PostgreSQL mock outbox by default; optional Gmail adapter |

`AgentState` is working memory, not a durable business ledger. It stores message history, the latest tool batch, run-bound customer-resolution evidence, retries keyed by normalized action signature, pending human interactions, and verification/completion evidence. LangGraph's `InMemorySaver` checkpoints this state during a running process. [`ConfiguredGraph`](app/agent/graph.py) uses the run ID as default `thread_id` so `Command(resume=...)` reaches the same checkpoint.

## Safety, failure handling, and human input

### Customer identity and write prerequisites

Before customer-scoped reads or writes, [`customer_resolution_gate()`](app/agent/graph.py) resolves the user's reference through Operations MCP. Resolution evidence is tied to the current `run_id`; it cannot authorize another customer or another run. Ambiguous or missing results route to [`clarify()`](app/agent/graph.py), which calls LangGraph `interrupt()` rather than arbitrarily selecting a legal entity.

Task creation additionally requires a successful `list_tasks(project_id)` observation for the exact project. With `business_checks` enabled (the CLI default), project/task actions must be in observed resolved-customer scope. These are deterministic preconditions, not model instructions.

### Structured errors, retries, and limits

Tools return JSON such as `success`, `error_type`, and `retryable`; retryability is never inferred from prose.

- `MAX_TOOL_STEPS = 20` bounds all tool executions in a run.
- `MAX_RETRIES_PER_ACTION = 3` permits an initial call plus three retries only after an explicit retryable transient failure.
- Repeated non-retryable/duplicate actions are blocked by normalized action signature.
- Operations MCP can inject transient failures using `NORTHSTAR_TRANSIENT_FAILURES` and a state file because stdio servers are short-lived subprocesses.
- Mixed tool batches are handled per write: successful `create_project`/`create_task` calls are read back even if sibling calls fail. See [`route_after_tool()`](app/agent/graph.py) and [`test_onboarding_verification.py`](tests/test_onboarding_verification.py).

This is bounded recovery, not an assertion that every error is safely repairable.

### Communication policy and approval

[`evaluate_email()`](app/policy/communication.py) returns deterministic `allow`, `approval_required`, or `deny` before `send_email` reaches Communication MCP. A verified billing/technical contact at the customer's known domain can be allowed; an unverified recipient requires approval; narrowly detected credential-like content is denied.

For approval-required work, the exact proposed tool arguments and reason are stored in `pending_approval_action`, then [`approval()`](app/agent/graph.py) interrupts. Resuming with `approve` or `reject` re-checks policy before MCP execution. Reject, deny, or remaining approval requirements never reach the side-effect boundary.

## Independent verification: execution is not completion

Northstar uses different verification paths for different outcomes. A successful tool response is an observation, not proof.

| Operation | Verification | On mismatch/missing evidence |
| --- | --- | --- |
| Project creation | [`_verify_created_project()`](app/agent/graph.py) lists projects and compares ID, resolved customer ID, name, and status. | `onboarding_verification_failed`; no automatic duplicate write. |
| Task creation | [`_verify_created_task()`](app/agent/graph.py) reads persisted project/task and compares ID, relationship, title, and status. | Same safe stop; no corrective task write. |
| Invoice logging | [`verify_invoice()`](app/agent/graph.py) gets the persisted invoice and compares customer, number, amount, dates, revision, status, and supersession relation. | `invoice_verification_failed`. |
| Invoice reconciliation (read-only) | [`reconcile_invoice()`](app/agent/graph.py) compares the selected active persisted invoice with the matching internal invoice Markdown document, including amount, due date, customer name, and active revision evidence. | `VERIFIED`, `FAILED`, or `INSUFFICIENT_EVIDENCE`; no write is attempted. |
| Mock email send | [`verify()`](app/agent/graph.py) calls `get_email` and compares recipient, subject, body, and status. | `communication_verification_failed`, or bounded repair for eligible observed mismatches. |

[`_verified_onboarding_claims()`](app/agent/graph.py) limits final creation claims to IDs with successful read-back evidence. A project found by `list_projects` must be described as reused, not created.

### Onboarding business-completeness review

Write verification proves an individual project/task was persisted as requested. It does **not** prove selected tasks meet all contract/SOP requirements.

Invoice reconciliation is a narrower business-evidence slice: it can establish consistency between the selected active internal invoice record and the available internal invoice document. It does not establish that the invoice is commercially or legally correct, and missing or contradictory evidence is never reported as verified.

For CLI runs, `business_checks` defaults to enabled. When model planning stops for a resolved onboarding goal, [`verify_requirements()`](app/agent/graph.py) independently:

1. reads the relevant full governing contract plus completion-reference documents through Knowledge MCP;
2. reads persisted tasks only from customer projects observed during this run through Operations MCP; and
3. calls a separate LLM reviewer with those documents and task records, not the agent's previous reasoning.

Missing named work items send a bounded repair instruction back to planning. `MAX_COMPLETION_REPAIRS = 2`; after that, the run stops with `onboarding_requirements_incomplete` and reports open work. A claimed task ID must exist in read-back records and its persisted title must contain requirement-specific, non-generic terms; a generic setup task cannot satisfy unrelated items merely because the reviewer cites its ID.

This is stronger than relying on executing-model prose, but it is not fully deterministic: requirements interpretation is an LLM review. If document retrieval or reviewer JSON parsing fails, the result is `SKIPPED`, not a pass. See [`test_onboarding_completion.py`](tests/test_onboarding_completion.py).

## Implemented workflows

### Customer onboarding

Illustrative request: `Onboard Globex Corporation LLC.`

1. The model requests customer information; the identity gate resolves the legal entity or interrupts for clarification.
2. Before onboarding writes, Northstar requires retrieved SOP and governing-contract evidence.
3. The model inspects existing projects and tasks, then may propose project/task writes.
4. The runtime requires exact-project task inspection and blocks out-of-scope projects when business checks are on.
5. Every successful project/task write is read back independently. A passed verifier returns to planning, supporting multi-step onboarding.
6. When planning ends, optional completion review compares run-scoped persisted tasks with contract/SOP-derived work items. It can request up to two bounded re-planning cycles; unresolved items are reported safely.

Tests cover no-duplicate behavior, multi-step onboarding, transient task recovery, prerequisites, customer resolution, persisted-write read-back, completion review/repair, scope checks, and mixed-result write batches. They do not demonstrate that every possible contract can be interpreted correctly by the LLM reviewer.

### Invoice inspection and logging

Illustrative request: `Inspect the latest active invoice for Acme Corporation Inc.`

The model can use `list_invoices` to inspect revision relationships and `get_invoice` to inspect a selected record. [`app/database.py`](app/database.py) seeds Acme with a superseded original invoice and active final revision `INV-2026-1018-R1`; the graph does not hardcode that selection. For a log action, `verify_invoice()` reads the persisted record and compares intended and actual fields. See [`test_task22_invoice_management.py`](tests/test_task22_invoice_management.py).

### Controlled communication

Illustrative request: `Send an onboarding update to Acme's verified technical contact.`

The runtime derives recipient context from customer records, applies policy, and invokes mock communication, interrupts for approval, or stops. A successful mock write is independently verified through `get_email`. Mock email is the default and persists only to the PostgreSQL `emails` table. Gmail is an optional backend behind the same MCP interface; it is not required for development/tests and can cause real external effects if intentionally configured.

## Engineering decisions and trade-offs

### Explicit graph routing instead of an unconstrained agent loop

**Problem:** A model can repeat failures, skip prerequisites, or report success without evidence.

**Approach:** LangGraph stores execution state and routes through named nodes and conditional edges. The model proposes actions; runtime code enforces identity, retrieval, policy, retry, verification, and termination.

**Trade-off:** The graph contains workflow-specific logic and is less flexible than a generic planner. That explicitness makes it easier to test and defend.

### MCP boundaries for knowledge and operations

**Problem:** Retrieval and side effects have different trust/testing needs.

**Approach:** Separate local FastMCP servers expose small stdio contracts; the graph uses one client layer rather than importing database operations directly.

**Trade-off:** Local subprocess transport is straightforward for a prototype, but not optimized for throughput or distributed deployment.

### Retrieval as an evidence gate

**Problem:** An onboarding model might act using generic knowledge or omit the customer contract.

**Approach:** The runtime checks actual returned chunks for both SOP and exact legal-entity contract evidence before onboarding writes.

**Trade-off:** Lexical matching can miss relevant language. Production use needs stronger retrieval, metadata, and evaluation.

### Read-back verification after side effects

**Problem:** A tool success response or model statement may not equal persisted reality.

**Approach:** Project, task, invoice, and email flows compare independently read persisted state with expected fields; business completeness is a separate review.

**Trade-off:** Extra reads and specialized verifiers cost latency and code, but provide concrete evidence and safer claims.

### Bounded recovery rather than blind retries

**Problem:** Blind retries and repairs can duplicate business actions.

**Approach:** Only structured transient failures retry; retry, email repair, and onboarding-completion repair limits are explicit. Duplicate/non-retryable repetitions are blocked.

**Trade-off:** Some potentially recoverable scenarios stop rather than improvise, intentionally.

## Technology inventory

| Component | Technology | Role |
| --- | --- | --- |
| Runtime | Python | Application, MCP servers, tests, database layer. |
| Orchestration | LangGraph | Stateful graph, conditional routing, in-memory checkpoints, interrupt/resume. |
| Model integration | LangChain + `langchain-openai` | Tool binding and OpenAI chat client. |
| Default model | `gpt-4o-mini` | Default in [`get_llm()`](app/agent/graph.py); override with `OPENAI_MODEL`. |
| Tool protocol | FastMCP | Local stdio servers and MCP client. |
| Retrieval | Custom lexical Python scorer | Markdown chunks, token overlap, filename boost, top-five cap. |
| Persistence | PostgreSQL + SQLAlchemy async engine + asyncpg | Customers, projects, tasks, invoices, mock emails. |
| Policy | Python regex/rules | Narrow communication recipient/domain and sensitive-content gate. |
| Tests | Python `unittest` / `IsolatedAsyncioTestCase` | Deterministic graph, MCP, database, and policy coverage. |
| Demo adapter | FastAPI + Uvicorn + static HTML/CSS/JavaScript | Thin HTTP/SSE presentation and human-input control layer. |
| Optional service | Gmail API / OAuth | Explicit opt-in communication backend; not needed for normal runs. |

## Setup and run

### Prerequisites

- Python environment capable of installing [`requirements.txt`](requirements.txt).
- A reachable PostgreSQL database for `DATABASE_URL`.
- An OpenAI API key for real-LLM runs.
- PowerShell commands below use the project's `.venv` convention.

Create a virtual environment and install dependencies:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure `.env` from the existing sanitized template:

```dotenv
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini
DATABASE_URL=postgresql://user:password@localhost:5432/northstar
COMMUNICATION_BACKEND=mock
```

`app.database` converts `postgresql://` to SQLAlchemy's `postgresql+asyncpg://` form internally. Do not commit real API keys, database passwords, Gmail OAuth credentials, or tokens. Gmail paths in [`.env.example`](.env.example) are only for controlled optional integration testing.

Initialize the configured database and seed demonstration customers/invoices:

```powershell
.venv\Scripts\python.exe -m app.database
```

Run the CLI. It launches needed MCP servers through stdio; no separate server terminal is required:

```powershell
.venv\Scripts\python.exe -m app.main "Does Acme already exist as a customer?"
.venv\Scripts\python.exe -m app.main "Onboard Globex Corporation LLC"
.venv\Scripts\python.exe -m app.main "Inspect the latest active invoice for Acme Corporation Inc."
```

The CLI displays clarification/approval interrupts and resumes the same `run_id`. `app/knowledge_demo.py` runs example lexical retrieval queries via Knowledge MCP:

```powershell
.venv\Scripts\python.exe -m app.knowledge_demo
```

### Safe smoke configuration and diagnostics

Smoke mode refuses to start unless `SMOKE_TEST_DATABASE_URL` names `northstar_smoke`; it also forces mock communication. Use it for isolated real-model experiments, not the development database:

```powershell
$env:NORTHSTAR_SMOKE_TEST_MODE = "1"
$env:SMOKE_TEST_DATABASE_URL = "postgresql://user:password@localhost:5432/northstar_smoke"
$env:NORTHSTAR_DIAGNOSTICS = "1"
$env:COMMUNICATION_BACKEND = "mock"
.venv\Scripts\python.exe -m app.main "Onboard Globex Corporation LLC"
```

For a no-write investigation, add `NORTHSTAR_SMOKE_TEST_READ_ONLY=1`; graph and database write paths reject customer, project, task, invoice, and email writes. Do not run `app.database` in smoke read-only mode.

Diagnostics are off by default. With `NORTHSTAR_DIAGNOSTICS=1`, one JSON report is emitted to stderr with run ID, tool names, sanitized arguments/results, interrupts/resumes, stop reason, and verification evidence. It omits bodies, document content, credentials/tokens, and full email addresses; it does not record model chain-of-thought.

### Deterministic transient-failure injection

For retry testing only:

```powershell
$env:NORTHSTAR_TRANSIENT_FAILURES = "create_task:2"
$env:NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE = "$env:TEMP\northstar-transient-state.json"
```

The first two matching Operations MCP calls return a structured retryable transient failure; the third can execute normally. Clear these variables afterward.

## Testing and validation

Run deterministic tests with mock communication explicitly selected. This prevents a shell-level Gmail configuration from changing mock-email tests:

```powershell
$env:COMMUNICATION_BACKEND = "mock"
Remove-Item Env:NORTHSTAR_GMAIL_INTEGRATION_TESTS -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py" -v
```

Useful focused commands:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_onboarding_verification -v
.venv\Scripts\python.exe -m unittest tests.test_onboarding_completion -v
.venv\Scripts\python.exe -m unittest tests.test_onboarding_prerequisites -v
.venv\Scripts\python.exe -m unittest tests.test_task11_transient_recovery -v
.venv\Scripts\python.exe -m unittest tests.test_task13_clarification -v
.venv\Scripts\python.exe -m unittest tests.test_task15_policy -v
.venv\Scripts\python.exe -m unittest tests.test_task22_invoice_management -v
.venv\Scripts\python.exe -m unittest tests.test_ui_api -v
```

Coverage includes retrieval; Operations MCP; customer matching/resolution; onboarding prerequisites, write verification, completion review, scope checks, and mixed-result batches; transient retries; clarification; communication policy/approval/verification/repair; diagnostics redaction; structured tool errors; and invoice revision/logging verification.

### Latest observed test status

The latest full-suite output supplied during documentation work ran **81 tests in 326.824 seconds**: the onboarding mixed-result tests passed, the Gmail integration test was skipped as intended, and four Communication tests errored. The shell selected the Gmail backend while those mock tests expected the mock result shape (`email`). This is an environment-configuration issue, not evidence of a passing full suite. The explicit mock command above has been documented, but it was **not rerun as part of this README task**.

The current [`test_onboarding_completion.py`](tests/test_onboarding_completion.py) exists but was not listed in that supplied 81-test output; its current status has not been independently established here.

The focused UI/API adapter suite was run after its implementation:

```text
Ran 4 tests in 0.088s
OK
```

It covers static UI serving, run submission and idempotent submission reuse, SSE event history, verified versus unverified reports, and same-run clarification/approval resumes using a fake graph. It does not call a real LLM, MCP server, or database.

After that, the focused UI, clarification, and onboarding-write-verification suite was run with local subprocess permission:

```text
Ran 12 tests in 5.711s
OK
```

This second command exercised the existing real Knowledge MCP subprocess boundary in the clarification tests. It was not a full regression-suite run.

## Demo UI

The optional demonstration UI is a thin FastAPI adapter plus a static, no-build frontend. It does not contain agent planning, tool-selection, retry, policy, or verification rules; it starts and resumes the same [`graph`](app/agent/graph.py) used by the CLI.

Start it from the project root:

```powershell
$env:COMMUNICATION_BACKEND = "mock"
.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The UI presents natural-language presets, streams actual node/tool/result/verification updates using SSE, displays backend verification evidence, and exposes genuine clarification or approval resumes through the same `run_id`/LangGraph `thread_id`. It remains a single-process demonstration: run metadata and event history are in memory, so a server restart loses UI history and interrupt checkpoints.

The CLI remains available independently through `python -m app.main ...`.

## Known limitations

- Retrieval is local lexical matching over Markdown. There are no embeddings, reranking, document version governance, access control, or retrieval-quality benchmarks.
- Workflows are narrow: customer operations, onboarding projects/tasks, invoices, and communication. There is no browser, GUI, filesystem-editing, or arbitrary API automation agent.
- LangGraph uses `InMemorySaver`; interrupts resume only while the process remains alive. Production needs durable checkpoints.
- PostgreSQL records and documents are prototype/simulated internal data. The initializer creates tables and seeds fixture-style customers/invoices; it is not a migration system.
- Mock communication records a sent status in the PostgreSQL outbox; it does not deliver email. Gmail is optional, opt-in, and needs controlled OAuth configuration.
- Communication policy is narrow deterministic matching and regex detection, not comprehensive authorization, DLP, or compliance.
- Onboarding write verification proves persisted fields, not full business completion. The separate completion review uses an LLM to derive applicable requirements, although a deterministic persisted-title evidence check rejects unsupported task-ID claims. It may still be `SKIPPED`; it is not a deterministic contract-completeness guarantee.
- Email repair is bounded and only for eligible observed mock-email mismatches. Onboarding write verification intentionally avoids automatic duplicate corrective writes.
- Real-model behavior varies by model, prompt, documents, and environment. Fake-model tests are not a broad evaluation program.
- The demo UI has no authentication, authorization, durable run history, multi-worker coordination, or production job queue. Run records and SSE history exist only in the API process.

## Assumptions

- The configured PostgreSQL database is reachable and suitable for the selected mode; smoke mode is used for isolated write-capable experiments.
- Markdown contains the governing current contract and applicable internal procedures.
- A user resuming an interrupt is authorized to clarify/approve; role management is not implemented.
- `COMMUNICATION_BACKEND=mock` is used for normal development/tests unless a controlled Gmail integration is intentional.
- The OpenAI model has credentials/network access for real runs. Most graph tests replace it with deterministic fake responses.

## What I would build next

1. Use durable LangGraph persistence and authenticate/authorize human approvers.
2. Add document metadata/versioning, stronger retrieval with evaluation data, and explicit authoritative-contract selection.
3. Build isolated real-model scenario evaluation with repeatability controls and outcome metrics.
4. Evolve policy into role-, tenant-, and data-classification controls with auditable approval records.
5. Add migrations, transaction/audit boundaries, service deployment, and production observability.
6. Combine structured requirement extraction with human review for high-risk contractual interpretations.

## Source map for technical discussion

| Question | Start here |
| --- | --- |
| How does the graph decide and route? | [`app/agent/graph.py`](app/agent/graph.py) |
| What state survives a node transition? | [`app/agent/state.py`](app/agent/state.py) |
| How are MCP subprocesses called? | [`app/agent/mcp_client.py`](app/agent/mcp_client.py) |
| How is retrieval scored? | [`app/retrieval/lexical.py`](app/retrieval/lexical.py) |
| What is persisted and seeded? | [`app/database.py`](app/database.py) |

## Public-release notes

### Repository structure

```text
app/agent/          LangGraph state, prompts, MCP client, orchestration, diagnostics
app/database.py     PostgreSQL schema creation, fixture seed data, and data access
app/retrieval/      Lexical Markdown retrieval
app/communication/  Optional Gmail adapter
api/                FastAPI/SSE demo adapter
mcp_servers/        Local stdio FastMCP servers
company_data/       Demo company, contract, policy, and invoice Markdown
tests/              unittest and async regression tests
ui/                 No-build static HTML/CSS/JavaScript frontend
```

### Environment variables

Copy [`.env.example`](.env.example) to `.env` and replace only the placeholders:

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Real LLM only | OpenAI credential; never commit it. |
| `OPENAI_MODEL` | Optional | Model name; defaults to `gpt-4o-mini`. |
| `DATABASE_URL` | Normal runs | PostgreSQL URL for the `northstar` database. |
| `SMOKE_TEST_DATABASE_URL` | Smoke mode | PostgreSQL URL for the database named `northstar_smoke`. |
| `COMMUNICATION_BACKEND` | Optional | `mock` by default; `gmail` is opt-in. |
| `NORTHSTAR_GMAIL_CREDENTIALS_PATH` | Gmail only | OAuth client JSON path. |
| `NORTHSTAR_GMAIL_TOKEN_PATH` | Gmail only | OAuth token cache path. |
| `NORTHSTAR_GMAIL_TEST_RECIPIENT` | Optional | Controlled Gmail integration recipient. |
| `NORTHSTAR_GMAIL_INTEGRATION_TESTS` | Optional | Set `1` to opt into live Gmail integration tests. |
| `NORTHSTAR_BUSINESS_CHECKS` | Optional | Defaults to `1`; set `0` only for narrow tests. |
| `NORTHSTAR_DIAGNOSTICS` | Optional | Set to `1` for sanitized observable-action diagnostics. |
| `NORTHSTAR_SMOKE_TEST_MODE` | Optional | Uses the isolated smoke database and forces mock communication. |
| `NORTHSTAR_SMOKE_TEST_READ_ONLY` | Optional | Blocks write-capable database operations. |
| `NORTHSTAR_TRANSIENT_FAILURES` | Tests only | Example: `create_task:2`. |
| `NORTHSTAR_TRANSIENT_FAILURE_STATE_FILE` | Tests only | State file for transient-failure injection. |

The application uses `python-dotenv` to load `.env`. `DATABASE_URL` is mandatory
for normal imports and startup. No frontend Node dependencies are required:
the UI is served as static files by FastAPI.

### Database setup, schema, and demo data

PostgreSQL must be running and the configured database must already exist. The
project has no Alembic or other migration directory. Running:

```powershell
.venv\Scripts\python.exe -m app.database
```

creates the current tables and seeds the demo customers and invoice fixtures.
This initializer is suitable for a fresh local demo database, not production
migrations. Do not run it against an existing database unless you understand
the idempotent seed behavior. The application never creates or resets the
database server itself.

### Knowledge base and MCP configuration

Knowledge documents are read from [`company_data/`](company_data) by
[`knowledge_server.py`](mcp_servers/knowledge_server.py), using the lexical
retriever in [`app/retrieval/lexical.py`](app/retrieval/lexical.py). The agent
starts the four local stdio MCP servers on demand through
[`app/agent/mcp_client.py`](app/agent/mcp_client.py); no separate MCP terminal
is required:

- Knowledge: search and read Markdown documents.
- Operations: customers, projects, tasks, and PostgreSQL records.
- Invoice: invoice operations.
- Communication: mock outbox or optional Gmail.

### Running services locally

The CLI is the smallest entry point:

```powershell
.venv\Scripts\python.exe -m app.main "Onboard Delta Health"
```

The browser demo starts the FastAPI adapter:

```powershell
$env:COMMUNICATION_BACKEND = "mock"
.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The frontend has no build step. The MCP
processes are child stdio processes of the CLI/API process.

### Tests and reproducibility

The supported test runner is Python `unittest`; tests do not require a live
OpenAI call, but database/MCP tests use the configured local environment.
For deterministic communication tests:

```powershell
$env:COMMUNICATION_BACKEND = "mock"
.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py" -v
```

The focused release-regression run covering the UI/API evidence flow,
onboarding completion, and invoice reconciliation completed successfully:
**54 tests, 0 failures, 0 errors**. The full suite was not rerun during this
release-preparation pass; the previously observed full-suite communication
errors remain documented below.

Python `3.13` is the currently validated interpreter in this workspace.
Dependencies are declared in [`requirements.txt`](requirements.txt). There is
no `package.json`, frontend lockfile, Dockerfile, or CI workflow in this
repository. A clean-machine setup has not been independently verified.

### What verification means

- `VERIFIED` invoice reconciliation means the selected active persisted invoice
  matched the source document on invoice number, amount, due date, customer
  name, and current revision/status.
- `FAILED` means the required invoice evidence was available but one or more
  fields differed.
- `INSUFFICIENT_EVIDENCE` means invoice selection, persistence, source-document,
  or required-field evidence was unavailable.
- Onboarding write verification proves that expected project/task fields were
  persisted and read back. It does **not** prove that the business work
  represented by a task was completed.
- The independent onboarding completion review checks applicable document-derived
  requirements against persisted task titles. It is not proof that the underlying
  reviews, tests, or approvals happened in the real world.

### Security considerations

`.env`, OAuth credentials/tokens, virtual environments, caches, logs, database
files, and private key/certificate formats are ignored by [`.gitignore`](.gitignore).
Do not commit API keys, database passwords, Gmail files, or diagnostic output
containing sensitive data. Diagnostics are opt-in and sanitize credentials,
document bodies, and full email addresses, but sanitization is not a guarantee
that a user can publish arbitrary logs safely.

The current workspace contains local secret material in ignored files. Rotate
any credential that has ever been exposed outside the local machine before
publishing, and verify the release candidate with a secret scanner. Because
this checkout is not a Git repository, no local Git tracking history or remote
configuration could be audited here.

### External services and prebuilt components

The project uses OpenAI through LangChain/LangChain OpenAI, LangGraph,
FastMCP, FastAPI/Uvicorn, SQLAlchemy/asyncpg, PostgreSQL, and optional Google
Gmail API OAuth libraries. The application also relies on their transitive
dependencies listed in the installed environment. These are external
frameworks/services, not code authored by this project.

### License status

No `LICENSE` file or explicit open-source license is present. Do not publish
the repository as open source until the copyright holder selects and adds an
appropriate license. [`app/database.py`](app/database.py) |
| How are policy decisions made? | [`app/policy/communication.py`](app/policy/communication.py) |
| How are diagnostics sanitized? | [`app/agent/diagnostics.py`](app/agent/diagnostics.py) |
| What documents drive the demo? | [`company_data/`](company_data) |
| What behavior is tested? | [`tests/`](tests) |
