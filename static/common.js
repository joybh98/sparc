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

function renderTool(call, result, live) {
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
  </div>`;
}

// opts: {open: bool, live: bool}. `live` = the run is still going (partial trace).
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
    ? `Trace · ${nCalls} tool call(s) so far · running…`
    : `Trace · ${final ? final.iterations : iters.size} iteration(s) · ${nCalls} tool call(s)`
      + (final ? ` · ${(final.latency_ms / 1000).toFixed(1)}s · stop: ${final.stop_reason}` : '');

  const body = [...iters.entries()].map(([n, it]) => {
    const m = it.model;
    const tokens = m && m.usage && m.usage.input_tokens ? ` · ${m.usage.input_tokens} in / ${m.usage.output_tokens} out` : '';
    return `<div class="iter">
      <div class="iter-head">Iteration ${n}${m ? ` · model ${m.latency_ms}ms${tokens}` : ''}</div>
      ${m && m.text ? `<div class="think">${esc(m.text)}</div>` : ''}
      ${it.calls.map(c => renderTool(c.call, c.result, live)).join('')}
      ${it.notes.map(t => `<div class="note">${esc(t)}</div>`).join('')}
    </div>`;
  }).join('');

  const open = opts.open || failed;
  return `<details class="trace"${open ? ' open' : ''}>
    <summary>${live ? '<span class="spin"></span>' : ''}${esc(summary)}${failed ? ` <span class="badge err">${esc(failed)}</span>` : ''}</summary>
    <div class="trace-body">${body || '<small class="hint">Waiting for the model…</small>'}
      ${live ? '<div class="note">⏳ working…</div>' : ''}</div>
  </details>`;
}

// Re-render a trace into a container, keeping whatever open/closed state the user chose.
function setTrace(host, trace, opts = {}) {
  const prev = host.querySelector('details.trace');
  host.innerHTML = renderTrace(trace, { ...opts, open: prev ? prev.open : opts.open });
}

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
