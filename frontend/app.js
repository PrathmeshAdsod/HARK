const $ = (selector) => document.querySelector(selector);
const state = { demoId: null, demo: null, loading: false };

const coldPrompt = "Investigate why our orders lookup became slower after today's deployment.";
const warmPrompt = "Checkout reads have regressed since the latest release.";

document.addEventListener("DOMContentLoaded", () => {
  $("#start-demo").addEventListener("click", startDemo);
  $("#fresh-demo").addEventListener("click", startDemo);
  $("#run-task").addEventListener("click", runTask);
  $("#copy-link").addEventListener("click", copyLink);
  window.addEventListener("popstate", route);
  route();
});

async function route() {
  const match = location.pathname.match(/^\/demo\/([A-Za-z0-9_-]{32})$/);
  if (!match) {
    state.demoId = null;
    state.demo = null;
    render();
    return;
  }
  state.demoId = match[1];
  await loadDemo();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "content-type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.message || "Hark could not complete this request.");
  return payload;
}

async function startDemo() {
  setLoading(true, "Starting a fresh investigation");
  try {
    const result = await api("/api/demos", { method: "POST", body: "{}" });
    history.pushState({}, "", result.url);
    state.demoId = result.demo_id;
    await loadDemo(false);
  } catch (error) {
    toast(error.message);
  } finally {
    setLoading(false);
  }
}

async function loadDemo(showLoader = true) {
  if (showLoader) setLoading(true, "Restoring this investigation from CockroachDB");
  try {
    state.demo = await api(`/api/demos/${state.demoId}`);
    render();
  } catch (error) {
    state.demo = null;
    toast(error.message);
    history.replaceState({}, "", "/");
    state.demoId = null;
    render();
  } finally {
    if (showLoader) setLoading(false);
  }
}

async function runTask() {
  if (state.loading) return;
  const task = $("#task-input").value.trim();
  setLoading(true, "Executing the official Agent Skill");
  try {
    state.demo = await api(`/api/demos/${state.demoId}/runs`, {
      method: "POST",
      body: JSON.stringify({ task }),
    });
    render();
    setTimeout(() => $("#results").scrollIntoView({ behavior: "smooth", block: "start" }), 30);
  } catch (error) {
    toast(error.message);
  } finally {
    setLoading(false);
  }
}

async function invalidateExperience(id) {
  setLoading(true, "Invalidating derived memory");
  try {
    state.demo = await api(`/api/demos/${state.demoId}/experiences/${id}/invalidate`, {
      method: "POST",
      body: "{}",
    });
    render();
    toast("Experience invalidated. Its provenance remains auditable, but it will no longer be retrieved.");
  } catch (error) {
    toast(error.message);
  } finally {
    setLoading(false);
  }
}

function render() {
  const active = Boolean(state.demoId && state.demo);
  $("#landing").classList.toggle("hidden", active);
  $("#workspace").classList.toggle("hidden", !active);
  $("#fresh-demo").classList.toggle("hidden", !active);
  $("#copy-link").classList.toggle("hidden", !active);
  if (!active) return;

  const demo = state.demo;
  const runs = demo.runs || [];
  const experiences = demo.experiences || [];
  $("#demo-short").textContent = `${demo.id.slice(0, 8)}…${demo.id.slice(-4)}`;
  $("#run-allowance").textContent = `${demo.limits.runs_used_24h} / ${demo.limits.runs_allowed_24h}`;
  $("#memory-count").textContent = String(experiences.filter((item) => !item.invalidated_at).length);

  const hasExperience = experiences.some((item) => !item.invalidated_at);
  $("#run-mode").textContent = hasExperience ? "Prior experience found" : "No prior experience";
  $("#task-input").value = hasExperience ? warmPrompt : coldPrompt;
  $("#composer-hint").textContent = hasExperience
    ? "Hark will search prior execution experience before the Skill runs."
    : "The first investigation starts without execution experience.";
  $("#run-task").disabled = runs.length >= demo.limits.runs_allowed_24h;

  renderMemories(experiences);
  renderRuns(runs);
  renderComparison(runs);
}

function renderMemories(experiences) {
  const list = $("#memory-list");
  if (!experiences.length) {
    list.innerHTML = `<div class="empty-memory"><div class="empty-glyph">H</div><b>No execution experience yet</b><p>The first investigation will leave compact, provenance-backed memory here.</p></div>`;
    return;
  }
  list.innerHTML = experiences
    .slice()
    .reverse()
    .map((item) => {
      const invalid = Boolean(item.invalidated_at);
      return `<article class="memory-card ${invalid ? "invalid" : ""}">
        <header><span>${invalid ? "Invalidated" : "Active experience"}</span><code>${escapeHtml(item.id.slice(0, 8))}</code></header>
        <h3>${escapeHtml(shorten(item.original_task, 78))}</h3>
        <p>${escapeHtml(item.experience_brief)}</p>
        <dl><div><dt>Source run</dt><dd>${escapeHtml(item.source_run_id.slice(0, 8))}</dd></div><div><dt>Used later</dt><dd>${item.times_used} run${Number(item.times_used) === 1 ? "" : "s"}</dd></div></dl>
        ${invalid ? "" : `<button class="button button-ghost" type="button" data-invalidate="${item.id}">Invalidate memory</button>`}
      </article>`;
    })
    .join("");
  list.querySelectorAll("[data-invalidate]").forEach((button) => {
    button.addEventListener("click", () => invalidateExperience(button.dataset.invalidate));
  });
}

function renderRuns(runs) {
  const results = $("#results");
  results.innerHTML = runs
    .slice()
    .reverse()
    .map((run, reverseIndex) => {
      const originalIndex = runs.length - reverseIndex - 1;
      const metrics = run.metrics || {};
      const warm = Boolean(metrics.memory_used);
      const failed = run.status === "failed";
      const events = (run.events || [])
        .map((event) => `<div class="trace-event ${escapeHtml(event.event_type)}"><b>${escapeHtml(event.title)}</b><p>${escapeHtml(event.detail)}</p></div>`)
        .join("");
      return `<article class="panel run-card">
        <header class="run-summary">
          <div><span class="run-tag ${failed ? "failed" : ""}"><i></i>${warm ? "Prior experience used" : "No prior experience"} · ${failed ? "did not complete" : "completed"}</span><h2>${escapeHtml(shorten(run.task, 78))}</h2></div>
          <div class="metric-strip"><div><b>${formatDuration(metrics.duration_ms)}</b><span>duration</span></div><div><b>${metrics.tool_calls ?? 0}</b><span>tool calls</span></div><div><b>${metrics.failures ?? 0}</b><span>failures</span></div></div>
        </header>
        <div class="run-body">
          <section class="trace"><h3>Execution trace · Run ${originalIndex + 1}</h3>${events || "<p>No events were recorded.</p>"}</section>
          <section class="diagnosis"><h3>${failed ? "Truthful failure" : "Evidence-grounded diagnosis"}</h3><blockquote class="${failed ? "error-copy" : ""}">${escapeHtml(run.diagnosis || run.error_message || "No diagnosis was produced.")}</blockquote></section>
        </div>
      </article>`;
    })
    .join("");
}

function renderComparison(runs) {
  const panel = $("#comparison");
  if (runs.length < 2) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }
  const [first, related] = runs.slice(-2);
  const firstMetrics = first.metrics || {};
  const relatedMetrics = related.metrics || {};
  panel.classList.remove("hidden");
  panel.innerHTML = `<header><h2>First vs related</h2><span>Measured from these executions</span></header>
    <div class="compare-grid">${comparisonRun("First · no precedent", firstMetrics)}<div class="compare-arrow">→</div>${comparisonRun(relatedMetrics.memory_used ? "Related · experience used" : "Related investigation", relatedMetrics)}</div>`;
}

function comparisonRun(label, metrics) {
  return `<div class="compare-run"><span>${label}</span><div class="compare-metrics"><div><b>${metrics.tool_calls ?? 0}</b><small>tools</small></div><div><b>${metrics.failures ?? 0}</b><small>failures</small></div><div><b>${formatDuration(metrics.duration_ms)}</b><small>time</small></div></div></div>`;
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(location.href);
    toast("Shareable investigation link copied.");
  } catch {
    toast("Copy was blocked by the browser. Use the current address-bar URL.");
  }
}

function setLoading(active, label) {
  state.loading = active;
  const layer = $("#loading");
  layer.classList.toggle("hidden", !active);
  if (active && label) layer.querySelector("strong").textContent = label;
}

let toastTimer;
function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.add("hidden"), 5500);
}

function formatDuration(milliseconds) {
  if (milliseconds == null) return "—";
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)}s` : `${Math.round(milliseconds)}ms`;
}

function shorten(value, length) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
