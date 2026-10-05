/* Shared UI pieces: helpers, the trace renderer, the agent picker, and the streaming
   run client. Nothing here knows about a specific modality. */

const $ = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));

function newId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 3 | 8)).toString(16);
  });
}

// The session id exists from page load: text chat works before any video is uploaded.
const App = {
  sessionId: newId(),
  modalities: [],
  // "Developer view" opens every reasoning trace by default. Clinicians get them collapsed.
  get devView() { try { return localStorage.getItem('sparc.devView') === '1'; } catch (e) { return false; } },
  set devView(on) { try { localStorage.setItem('sparc.devView', on ? '1' : '0'); } catch (e) { /* private mode */ } },
  feedback: {},     // turn_index -> {rating, reason, correction} for answers flagged this page load
  verdicts: {},     // call_id -> reviewer verdict, for calls marked this page load
  modality(name) { return this.modalities.find(m => m.name === name); },
};

function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmt(s) {
  s = Number(s) || 0;
  const m = Math.floor(s / 60);
  const r = (s % 60).toFixed(1);
  return `${m}:${r.padStart(4, '0')}`;
}

// Escapes text and turns moments in it (1:23, 1:23.5, 12.5s, 12.5–15s) into links that seek
// the player. Plain escaped text when there is no player to seek.
const TIME_RE = /\b(\d+(?:\.\d+)?)\s?[–-]\s?\d+(?:\.\d+)?\s?s\b|\b(\d{1,3}):([0-5]\d(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s?(?:s|sec|secs|seconds)\b/g;

function linkify(text) {
  text = String(text == null ? '' : text);
  if (!$('clipVideo')) return esc(text);
  let out = '', last = 0, m;
  TIME_RE.lastIndex = 0;
  while ((m = TIME_RE.exec(text))) {
    const sec = m[1] !== undefined ? m[1] : m[2] !== undefined ? Number(m[2]) * 60 + Number(m[3]) : m[4];
    out += esc(text.slice(last, m.index))
      + `<a href="#" class="ts" data-seek="${esc(sec)}" title="jump the player here">${esc(m[0])}</a>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

function confHTML(c) {
  if (typeof c !== 'number') return '—';
  const level = c >= 0.75 ? 'high' : c >= 0.45 ? 'mid' : 'low';
  return `<span class="conf ${level}">${Math.round(c * 100)}%</span>`;
}

// The frames a step or answer rests on: thumbnails that seek the player, with notes.
function evidenceHTML(evidence) {
  evidence = evidence || [];
  if (!evidence.length) return '<small class="hint">none cited</small>';
  return evidence.map(e => `<div class="ev">
    <img loading="lazy" data-seek="${esc(e.time_sec)}" src="${esc(frameUrl({ time_sec: e.time_sec }, 120))}" title="click to seek the video" />
    <span><a href="#" data-seek="${esc(e.time_sec)}">${fmt(e.time_sec)}</a><br><small class="hint">${linkify(e.note)}</small></span></div>`).join('');
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  return res.json();
}

function selectedModel() {
  return ($('modelSelect') && $('modelSelect').value) || 'gpt';
}

/* ------------------------------------------------------------ trace panel */

function seekTo(t) {
  const v = $('clipVideo');
  if (!v) return;
  v.currentTime = Number(t) || 0;
  v.pause();
  v.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function frameUrl(ref, w) {
  const q = new URLSearchParams({ t: ref.time_sec, w });
  if (ref.zoom) q.set('zoom', ref.zoom);
  if (ref.cx != null) q.set('cx', ref.cx);
  if (ref.cy != null) q.set('cy', ref.cy);
  return `/api/sessions/${App.sessionId}/frame?${q}`;
}

const VERDICTS = ['correct', 'incorrect', 'unsure'];

function markButtons(callId) {
  const cur = App.verdicts[callId];
  return `<div class="verdicts" data-call="${esc(callId)}"><span class="hint">your call:</span>
    ${VERDICTS.map(v => `<button class="verdict ${v}${cur === v ? ' active' : ''}" data-verdict="${v}">${v}</button>`).join('')}</div>`;
}

function renderTool(call, result, live, markId) {
  const argStr = JSON.stringify(call.args || {});
  const shortArgs = argStr.length > 160 ? argStr.slice(0, 160) + '…' : argStr;
  const seek = result && result.ui && result.ui.seek_to != null
    ? `<button class="seek" data-seek="${esc(result.ui.seek_to)}">▶ ${fmt(result.ui.seek_to)}</button>` : '';
  const thumbs = result && (result.images || []).length
    ? `<div class="thumbs">${result.images.map(ref => `
        <div class="thumb"><img loading="lazy" data-seek="${esc(ref.time_sec)}"
          src="${esc(frameUrl(ref, 240))}" title="click to seek the video" />
          ${fmt(ref.time_sec)}${ref.zoom ? ` · ${esc(ref.zoom)}x` : ''}
          <a href="${esc(frameUrl(ref, 960))}" target="_blank" rel="noopener">↗</a></div>`).join('')}</div>` : '';
  return `<div class="tool${result && result.is_error ? ' is-error' : ''}">
    <span class="name">${esc(call.name)}</span>${seek}
    ${result ? `<span class="badge" style="margin-left:6px;">${result.latency_ms}ms</span>` : ''}
    ${!result && live ? '<span class="badge warn" style="margin-left:6px;">running…</span>' : ''}
    ${result && result.is_error ? '<span class="badge err">error</span>' : ''}
    <div class="args" title="${esc(argStr)}">${esc(shortArgs)}</div>
    ${result && result.text ? `<div class="result">${esc(result.text)}</div>` : ''}
    ${thumbs}
    ${markId && result ? markButtons(markId) : ''}
  </div>`;
}

const roundSec = x => Math.round(Number(x) || 0);

// What the agent is doing, in words a clinician can read. Keyed by tool name; a tool
// without an entry falls back to a generic line.
const TOOL_STATUS = {
  get_clip_info: () => 'Checking the clip details…',
  sample_frames: a => `Looking at frames ${roundSec(a.start_sec)}–${roundSec(a.end_sec)}s…`,
  zoom_frame: a => `Zooming in on the frame at ${roundSec(a.time_sec)}s…`,
  submit_steps: () => 'Writing up the step timeline…',
  submit_answer: () => 'Writing up the answer…',
  get_prior_analysis: () => 'Re-reading the earlier analysis…',
  get_reviewer_marks: () => 'Checking your marked steps…',
  ask_user: () => 'Preparing a question for you…',
  search_history: () => 'Searching the conversation…',
  calculate: () => 'Calculating…',
};

// One short line describing the latest thing the agent did, from the events so far.
function statusFor(trace) {
  const last = (trace || []).filter(e => e.type !== 'final').pop();
  if (!last) return 'Starting…';
  if (last.type === 'tool_call') {
    const f = TOOL_STATUS[last.name];
    return f ? f(last.args || {}) : `Using ${last.name}…`;
  }
  if (last.type === 'nudge') return 'Finishing up…';
  if (last.type === 'error') return 'Something went wrong…';
  if (last.type === 'model_call' && !last.n_tool_calls) return 'Writing up the result…';
  return 'Thinking…';
}

// opts: {open: bool, live: bool, turnIndex: int}. `live` = the run is still going (partial
// trace). `turnIndex` (set once the turn is saved) turns on per-call marking. The trace is
// collapsed unless `open` is set or the developer view is on; while live, a one-line status
// shows instead.
function renderTrace(trace, opts = {}) {
  trace = trace || [];
  const iters = new Map();   // iteration -> {model, calls: [{call, result}], notes: []}
  let final = null;
  const get = n => { if (!iters.has(n)) iters.set(n, { model: null, calls: [], notes: [] }); return iters.get(n); };

  trace.forEach(e => {
    if (e.type === 'final') { final = e; return; }
    const it = get(e.iteration || 0);
    if (e.type === 'model_call') it.model = e;
    else if (e.type === 'tool_call') it.calls.push({ call: e, result: null });
    else if (e.type === 'tool_result') {
      const slot = it.calls.find(c => c.call.id === e.id && !c.result);
      if (slot) slot.result = e; else it.calls.push({ call: { name: e.name, args: {} }, result: e });
    } else if (e.type === 'nudge') it.notes.push(`↻ ${e.text}`);
    else if (e.type === 'error') it.notes.push(`✖ ${e.stage || 'error'}: ${e.message}`);
  });

  const live = !!opts.live && !final;
  const nCalls = trace.filter(e => e.type === 'tool_call').length;
  const failed = final && final.error;
  const summary = live
    ? `Inspect reasoning · ${nCalls} tool call(s) so far`
    : `Inspect reasoning · ${final ? final.iterations : iters.size} iteration(s) · ${nCalls} tool call(s)`
      + (final ? ` · ${(final.latency_ms / 1000).toFixed(1)}s · stop: ${final.stop_reason}` : '');

  const body = [...iters.entries()].map(([n, it]) => {
    const m = it.model;
    const tokens = m && m.usage && m.usage.input_tokens ? ` · ${m.usage.input_tokens} in / ${m.usage.output_tokens} out` : '';
    return `<div class="iter">
      <div class="iter-head">Iteration ${n}${m ? ` · model ${m.latency_ms}ms${tokens}` : ''}</div>
      ${m && m.text ? `<div class="think">${esc(m.text)}</div>` : ''}
      ${it.calls.map(c => renderTool(c.call, c.result, live,
        opts.turnIndex != null && c.call.seq != null ? `${App.sessionId}:${opts.turnIndex}:${c.call.seq}` : null)).join('')}
      ${it.notes.map(t => `<div class="note">${esc(t)}</div>`).join('')}
    </div>`;
  }).join('');

  const open = opts.open != null ? opts.open : App.devView;
  const status = live ? `<div class="trace-status"><span class="spin"></span>${esc(statusFor(trace))}</div>` : '';
  return `${status}<details class="trace"${open ? ' open' : ''}>
    <summary>${esc(summary)}${failed ? ` <span class="badge err">${esc(failed)}</span>` : ''}</summary>
    <div class="trace-body">${body || '<small class="hint">Waiting for the model…</small>'}
      ${live ? '<div class="note">⏳ working…</div>' : ''}</div>
  </details>`;
}

// Re-render a trace into a container, keeping whatever open/closed state the user chose.
function setTrace(host, trace, opts = {}) {
  host.innerHTML = renderTrace(trace, { ...opts, open: traceOpen(host, opts.open) });
}

// Whether the trace in `host` is open right now (undefined if it has none yet), so a
// re-render, or a card rebuilt from the saved turn, keeps the reviewer's choice.
function traceOpen(host, fallback) {
  const prev = host.querySelector('details.trace');
  return prev ? prev.open : fallback;
}

// reviewer verdict on one call; clicking the active verdict clears it
document.addEventListener('click', async e => {
  const btn = e.target.closest('button.verdict');
  if (!btn) return;
  const host = btn.closest('.verdicts');
  const id = host.dataset.call;
  const verdict = App.verdicts[id] === btn.dataset.verdict ? null : btn.dataset.verdict;
  const r = await postJSON('/api/calls/mark', { call_id: id, verdict });
  if (r.error) return alert(r.error);
  if (verdict) App.verdicts[id] = verdict; else delete App.verdicts[id];
  host.querySelectorAll('button.verdict').forEach(b => b.classList.toggle('active', b.dataset.verdict === verdict));
});

// any [data-seek] element inside a trace or step table seeks the player
document.addEventListener('click', e => {
  const el = e.target.closest('[data-seek]');
  if (!el) return;
  e.preventDefault();
  seekTo(el.dataset.seek);
});

/* ------------------------------------------------------------ streaming run client */

// Starts an agent run and polls until it finishes, calling onUpdate(allEventsSoFar) as the
// trace grows. Resolves with the saved turn; rejects with an Error on failure.
async function runAgent(body, onUpdate) {
  const start = await postJSON('/api/query', {
    ...body, mode: 'agent', stream: true, session_id: App.sessionId, models: [selectedModel()],
  });
  if (start.error) throw new Error(start.error);

  const events = [];
  let after = 0;
  for (;;) {
    const res = await fetch(`/api/runs/${start.run_id}?after=${after}`);
    if (!res.ok) throw new Error(`lost track of the run (HTTP ${res.status})`);
    const r = await res.json();
    if (r.events.length) {
      events.push(...r.events);
      after = events[events.length - 1].seq;
      if (onUpdate) onUpdate(events);
    }
    if (r.status === 'done') return r.turn;
    if (r.status === 'error') throw new Error(r.error || 'agent run failed');
    await sleep(350);
  }
}

/* ------------------------------------------------------------ agent picker */

// Lists agents (filtered), shows the selected agent's JSON in an editor, and returns
// either {agent: name} or, if the JSON was edited, {agent_config: {...}}.
class AgentPicker {
  constructor(host, { filter, label = 'Agent' }) {
    this.filter = filter;
    this.original = '';
    this.agents = [];
    host.innerHTML = `
      <div class="agent-bar"><label>${esc(label)}</label><select></select></div>
      <details class="cfg"><summary>Agent config (edit to run a custom agent)</summary>
        <textarea rows="12" spellcheck="false"></textarea><small class="hint"></small></details>`;
    this.sel = host.querySelector('select');
    this.box = host.querySelector('details.cfg');
    this.cfg = host.querySelector('textarea');
    this.hint = host.querySelector('small.hint');
    this.sel.addEventListener('change', () => this.showConfig());
  }

  populate(modality) {
    this.modality = modality;
    this.agents = modality.agents.filter(this.filter);
    this.sel.innerHTML = this.agents.map(a =>
      `<option value="${esc(a.name)}" title="${esc(a.description)}">${esc(a.name)} — ${esc(a.description)}</option>`
    ).join('');
    this.showConfig();
    const bad = modality.invalid || [];
    if (bad.length) this.hint.textContent = bad.map(b => `⚠ ${b.file}: ${b.errors.join('; ')}`).join(' | ');
  }

  showConfig() {
    const a = this.agents.find(x => x.name === this.sel.value);
    this.box.style.display = a ? '' : 'none';
    this.original = a ? JSON.stringify(a, null, 2) : '';
    this.cfg.value = this.original;
    this.hint.textContent = a ? `Available tools: ${this.modality.tools.map(t => t.name).join(', ')}` : '';
  }

  request() {
    if (this.cfg.value.trim() !== this.original.trim()) {
      try { return { agent_config: JSON.parse(this.cfg.value) }; }
      catch (err) { throw new Error('Agent config is not valid JSON: ' + err.message); }
    }
    return { agent: this.sel.value };
  }
}
