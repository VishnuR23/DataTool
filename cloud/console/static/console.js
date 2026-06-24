// cloud/console/static/console.js — SSE client; vanilla, no framework (spec)
const feed = document.getElementById("feed");
const status = document.getElementById("status");
const filter = document.getElementById("filter");

const GLYPH = { ramp: "↑", hold: "⏸", promote: "✓", revert: "⟲", guardrail: "⚠" };

function render(ev) {
  const term = (filter.value || "").trim();
  if (term && (ev.experiment_id || "").indexOf(term) === -1) return;
  const line = document.createElement("div");
  line.className = "line";
  const glyph = GLYPH[ev.kind] || "•";
  line.innerHTML =
    `<span class="ts">${ev.occurred_at}</span> ` +
    `<span class="glyph">${glyph}</span> ` +
    `${ev.experiment_id ? "[" + ev.experiment_id + "] " : ""}${ev.summary}`;
  feed.appendChild(line);
  window.scrollTo(0, document.body.scrollHeight);
}

const source = new EventSource("/console/stream");
source.onopen = () => { status.textContent = "live"; status.className = "status live"; };
source.onerror = () => { status.textContent = "reconnecting"; status.className = "status down"; };
source.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
