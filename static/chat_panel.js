/* Chat with the model. One instance lives for the whole page; it is re-mounted into
   whichever modality panel is active and re-pointed at that modality's chat agents, so
   the conversation thread survives the switch from text to video. */

class ChatPanel {
  constructor() {
    this.suggestions = [];
    this.busy = false;
    this.root = document.createElement('div');
    this.root.className = 'panel';
    this.root.innerHTML = `
      <h3 data-title>Chat</h3>
      <div data-picker></div>
      <div class="chips" data-chips style="display:none;"></div>
      <textarea rows="3" data-question></textarea>
      <div class="send-row"><button data-send>Send message</button><small class="hint" data-status></small></div>
      <div data-thread></div>`;
    const q = sel => this.root.querySelector(sel);
    this.titleEl = q('[data-title]');
    this.chipsEl = q('[data-chips]');
    this.questionEl = q('[data-question]');
    this.sendBtn = q('[data-send]');
    this.statusEl = q('[data-status]');
    this.threadEl = q('[data-thread]');
    this.picker = new AgentPicker(q('[data-picker]'), {
      filter: a => a.kind !== 'steps', label: 'Answer with' });

    this.sendBtn.addEventListener('click', () => this.send());
    this.questionEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) this.send();
    });
    this.chipsEl.addEventListener('click', e => {
      const chip = e.target.closest('[data-chip]');
      if (!chip) return;
      this.questionEl.value = this.suggestions[Number(chip.dataset.chip)];
      this.questionEl.focus();
    });
  }

  // Point the chat at a modality: its agents, title, placeholder and suggestion chips.
  setModality(modality, { title, placeholder, suggestions = [] }) {
    this.picker.populate(modality);
    this.titleEl.textContent = title;
    this.questionEl.placeholder = placeholder;
    this.suggestions = suggestions;
    this.showChips(false);
  }

  showChips(on) {
    this.chipsEl.style.display = on && this.suggestions.length ? '' : 'none';
    this.chipsEl.innerHTML = this.suggestions.map((s, i) =>
      `<button class="chip" data-chip="${i}">${esc(s)}</button>`).join('');
  }

  // Block sending while something else (e.g. the analysis run) is using the session.
  setBlocked(on, why = '') {
    this.blocked = on;
    this.sendBtn.disabled = on || this.busy;
    this.statusEl.textContent = on ? why : '';
  }

  card(turn) {
    const badges = [
      `<span class="badge">${esc(turn.agent)}</span>`,
      `<span class="badge">${esc(turn.model)}</span>`,
      `<span class="badge">${turn.latency_ms}ms</span>`,
      `<span class="badge">${turn.iterations} iteration(s)</span>`,
      turn.needs_reply ? '<span class="badge warn">asked you a question — reply below</span>' : '',
    ].join('');
    return `<div class="response-card">
      <div class="q">Q${turn.turn_index}: ${esc(turn.question)}</div>
      <div class="meta">${badges} ${esc(turn.provider)}</div>
      ${turn.error ? `<div class="error">Error: ${esc(turn.error)}</div>` : ''}
      ${turn.answer ? `<div class="answer">${esc(turn.answer)}</div>` : ''}
      <div data-trace>${renderTrace(turn.trace)}</div>
    </div>`;
  }

  async send() {
    const question = this.questionEl.value.trim();
    if (!question || this.busy || this.blocked) return;

    let body;
    try { body = { question, ...this.picker.request() }; }
    catch (err) { return alert(err.message); }

    this.threadEl.insertAdjacentHTML('beforeend', `<div class="response-card">
      <div class="q">${esc(question)}</div>
      <div class="meta"><span class="badge warn">working…</span></div>
      <div data-trace></div></div>`);
    const cardEl = this.threadEl.lastElementChild;
    const traceHost = cardEl.querySelector('[data-trace]');
    cardEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    this.questionEl.value = '';
    this.busy = true;
    this.sendBtn.disabled = true;

    try {
      const turn = await runAgent(body, ev => setTrace(traceHost, ev, { open: true, live: true }));
      cardEl.outerHTML = this.card(turn);
    } catch (err) {
      cardEl.querySelector('.meta').innerHTML = `<span class="badge err">failed</span>`;
      cardEl.insertAdjacentHTML('beforeend', `<div class="error">${esc(err.message)}</div>`);
      this.questionEl.value = question;     // let the user retry
    } finally {
      this.busy = false;
      this.sendBtn.disabled = !!this.blocked;
    }
  }
}
