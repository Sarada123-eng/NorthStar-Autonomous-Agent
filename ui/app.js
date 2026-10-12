const goal = document.querySelector("#goal");
const start = document.querySelector("#start");
const events = document.querySelector("#events");
const status = document.querySelector("#run-status");
const runId = document.querySelector("#run-id");
const connection = document.querySelector("#connection");
const formError = document.querySelector("#form-error");
const report = document.querySelector("#report");
const verification = document.querySelector("#verification");
const evidence = document.querySelector("#evidence");
const intervention = document.querySelector("#intervention");
const interventionMessage = document.querySelector("#intervention-message");
const pendingDetails = document.querySelector("#pending-details");
const clarificationControls = document.querySelector("#clarification-controls");
const approvalControls = document.querySelector("#approval-controls");
const clarification = document.querySelector("#clarification");
const interventionError = document.querySelector("#intervention-error");
let currentRunId = null;
let source = null;
let submissionId = null;
let lastEventSequence = 0;

const presets = document.querySelectorAll("[data-goal]");
presets.forEach((button) => button.addEventListener("click", () => { goal.value = button.dataset.goal; goal.focus(); }));

function setStatus(value) { status.textContent = value.replaceAll("_", " "); status.className = `pill ${value}`; }
function clearTrace() { events.innerHTML = ""; lastEventSequence = 0; }
function clearEvidence() { evidence.innerHTML = '<p class="muted">No documents retrieved yet.</p>'; }
function renderEvidence(sources) {
  evidence.innerHTML = sources.map((item) => {
    const score = item.score == null ? "" : ` <span class="muted">score ${item.score}</span>`;
    return `<details class="evidence-item"><summary></summary><div class="evidence-meta">${item.source}${score}</div><pre class="evidence-excerpt"></pre></details>`;
  }).join("");
  [...evidence.querySelectorAll("summary")].forEach((node, index) => {
    node.textContent = sources[index].source;
    node.parentElement.querySelector(".evidence-excerpt").textContent = sources[index].excerpt;
  });
}
function addEvent(event) {
  if (!Number.isInteger(event.sequence) || event.sequence <= lastEventSequence) return;
  lastEventSequence = event.sequence;
  const item = document.createElement("li"); item.className = event.type;
  const payload = event.payload || {};
  if (event.type === "evidence" && Array.isArray(payload.sources)) {
    const existing = [...evidence.querySelectorAll(".evidence-item")].map((item) => item.dataset.source);
    const newSources = payload.sources.filter((item) => !existing.includes(item.source));
    if (newSources.length) {
      const current = [...evidence.querySelectorAll(".evidence-item")].map((item) => ({
        source: item.dataset.source,
        excerpt: item.querySelector(".evidence-excerpt").textContent,
      }));
      renderEvidence([...current, ...newSources]);
      [...evidence.querySelectorAll(".evidence-item")].forEach((item, index) => { item.dataset.source = [...evidence.querySelectorAll("summary")][index].textContent; });
    }
  }
  item.innerHTML = `<div class="event-head"><span class="event-type">${event.type.replaceAll("_", " ")}</span><time class="event-time">${new Date(event.timestamp).toLocaleTimeString()}</time></div><pre class="event-data"></pre>`;
  item.querySelector("pre").textContent = JSON.stringify(payload, null, 2);
  events.append(item); events.scrollTop = events.scrollHeight;
}
function renderReport(data) {
  const rows = [["Status", data.status], ["Outcome", data.outcome], ["Stop reason", data.execution_stop_reason || "None"], ["Summary", data.summary || "No final model summary available."], ["Error", data.error || "None"]];
  report.innerHTML = rows.map(([name, value]) => `<div><dt>${name}</dt><dd></dd></div>`).join("");
  [...report.querySelectorAll("dd")].forEach((node, index) => { node.textContent = rows[index][1]; });
  if (data.verification && Object.values(data.verification).some(Boolean)) { verification.textContent = JSON.stringify(data.verification, null, 2); verification.classList.remove("hidden"); } else { verification.classList.add("hidden"); }
  setStatus(data.status);
  if (data.pending) showIntervention(data.pending); else intervention.classList.add("hidden");
}
function showIntervention(pending) {
  intervention.classList.remove("hidden"); interventionError.textContent = "";
  interventionMessage.textContent = pending.question || pending.message || pending.reason || "Human input is required.";
  pendingDetails.textContent = JSON.stringify(pending, null, 2);
  clarificationControls.classList.toggle("hidden", pending.type !== "clarification");
  approvalControls.classList.toggle("hidden", pending.type !== "approval");
}
async function refresh() {
  if (!currentRunId) return;
  const response = await fetch(`/api/runs/${currentRunId}`);
  if (!response.ok) throw new Error(`Status request failed (${response.status})`);
  renderReport(await response.json());
}
function watch() {
  if (source) source.close();
  source = new EventSource(`/api/runs/${currentRunId}/events?after=${lastEventSequence}`);
  const types = ["run_started", "run_resumed", "node_update", "tool_selected", "tool_result", "tool_failure", "retryable_failure", "human_input_required", "verification", "evidence", "execution_stopped", "run_status", "run_error"];
  types.forEach((type) => source.addEventListener(type, async (message) => { addEvent(JSON.parse(message.data)); await refresh(); }));
  source.onerror = async () => { connection.textContent = "Trace reconnecting"; connection.className = "pill awaiting_human_input"; try { await refresh(); } catch (_) {} };
}
async function startRun() {
  formError.textContent = ""; const text = goal.value.trim();
  if (text.length < 3) { formError.textContent = "Enter a goal with at least three characters."; return; }
  start.disabled = true; intervention.classList.add("hidden"); verification.classList.add("hidden"); clearTrace(); clearEvidence(); setStatus("pending");
  submissionId = crypto.randomUUID();
  try {
    const response = await fetch("/api/runs", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({goal:text, submission_id:submissionId}) });
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Unable to start run.");
    currentRunId = data.run_id; runId.textContent = `Run ${currentRunId}`; watch(); await refresh();
  } catch (error) { formError.textContent = error.message; setStatus("failed"); } finally { start.disabled = false; }
}
async function resume(path, body) {
  interventionError.textContent = "";
  try {
    const response = await fetch(`/api/runs/${currentRunId}/${path}`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Unable to resume run.");
    intervention.classList.add("hidden"); setStatus("resuming"); watch(); await refresh();
  } catch (error) { interventionError.textContent = error.message; }
}
start.addEventListener("click", startRun);
document.querySelector("#submit-clarification").addEventListener("click", () => resume("clarification", {response:clarification.value.trim()}));
document.querySelector("#approve").addEventListener("click", () => resume("approval", {decision:"approve"}));
document.querySelector("#reject").addEventListener("click", () => resume("approval", {decision:"reject"}));
fetch("/api/health").then((response) => { if (!response.ok) throw new Error(); connection.textContent = "API connected"; connection.className = "pill completed"; }).catch(() => { connection.textContent = "API unavailable"; connection.className = "pill failed"; });
