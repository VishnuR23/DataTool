// cloud/console/static/console.js — SSE client; vanilla, no framework (project rule).
//
// The stream closes on idle and the browser reconnects, re-sending the backfill
// each time. We dedup by (source, source_id) so a reconnect never re-appends a
// line the console already shows — mirroring the panel's server-side dedup key.

const feed = document.getElementById("feed");
const statusEl = document.getElementById("status");
const filterEl = document.getElementById("filter");

const GLYPH = { ramp: "↑", hold: "⏸", promote: "✓", revert: "⟲", guardrail: "⚠" };

const seen = new Set();
let term = "";

function matches(expId) {
  return !term || (expId || "").indexOf(term) !== -1;
}

function shortTime(iso) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toTimeString().slice(0, 8);
}

function setStatus(text, cls) {
  statusEl.className = "status " + cls;
  statusEl.replaceChildren();
  const lamp = document.createElement("span");
  lamp.className = "lamp";
  statusEl.append(lamp, document.createTextNode(text));
}

function span(cls, text, title) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
  if (title) el.title = title;
  return el;
}

function render(ev) {
  const key = ev.source + ":" + ev.source_id;
  if (seen.has(key)) return; // already shown; skip re-delivered backfill
  seen.add(key);

  const line = document.createElement("div");
  line.className = "line";
  line.dataset.kind = ev.kind;
  line.dataset.exp = ev.experiment_id || "";
  if (!matches(ev.experiment_id)) line.classList.add("hidden");

  line.append(
    span("ts", shortTime(ev.occurred_at), ev.occurred_at),
    span("glyph", GLYPH[ev.kind] || "•"),
  );
  const body = document.createElement("span");
  body.className = "summary";
  if (ev.experiment_id) body.append(span("exp", "[" + ev.experiment_id + "] "));
  body.append(document.createTextNode(ev.summary));
  line.append(body);

  const atBottom = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 4;
  feed.appendChild(line);
  if (atBottom) feed.scrollTop = feed.scrollHeight; // live tail, unless scrolled up
}

filterEl.addEventListener("input", () => {
  term = filterEl.value.trim();
  for (const line of feed.children) {
    line.classList.toggle("hidden", !matches(line.dataset.exp));
  }
});

const source = new EventSource("/console/stream");
source.onopen = () => setStatus("live", "live");
source.onerror = () => setStatus("reconnecting", "down");
source.onmessage = (e) => {
  try {
    render(JSON.parse(e.data));
  } catch (_) {
    /* ignore malformed frame */
  }
};
