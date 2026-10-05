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
      <div class="ctx" data-ctx style="display:none;">
        <label><input type="checkbox" data-use-pos checked /> 📍 Use the player position
          <b data-pos>0:00.0</b> as context</label>
        <img data-pos-img alt="" />
      </div>
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
    this.ctxEl = q('[data-ctx]');
    this.usePosEl = q('[data-use-pos]');
    this.posEl = q('[data-pos]');
    this.posImgEl = q('[data-pos-img]');
    this.video = null;
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

  // Tie the chat to the video player: shows where it is and sends that moment with each
  // question. Pass null when there is no player.
  setPlayer(video) {
    this.video = video || null;
    this.ctxEl.style.display = video ? '' : 'none';
    if (!video) return;
    let timer;
    const label = () => { this.posEl.textContent = fmt(video.currentTime); };
    const thumb = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        this.posImgEl.src = frameUrl({ time_sec: video.currentTime.toFixed(2) }, 120);
      }, 200);
    };
    video.addEventListener('timeupdate', label);
    ['seeked', 'pause'].forEach(ev => video.addEventListener(ev, () => { label(); thumb(); }));
    label();
    thumb();
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
      turn.confidence != null ? `<span class="badge">confidence ${confHTML(turn.confidence)}</span>` : '',
      turn.player_time_sec != null
        ? `<span class="badge">📍 asked at <a href="#" data-seek="${esc(turn.player_time_sec)}">${fmt(turn.player_time_sec)}</a></span>` : '',
      turn.needs_reply ? '<span class="badge warn">asked you a question — reply below</span>' : '',
    ].join('');
    return `<div class="response-card">
      <div class="q">Q${turn.turn_index}: ${esc(turn.question)}</div>
      <div class="meta">${badges} ${esc(turn.provider)}</div>
      ${turn.error ? `<div class="error">Error: ${esc(turn.error)}</div>` : ''}
      ${turn.answer ? `<div class="answer">${linkify(turn.answer)}</div>` : ''}
      ${turn.uncertainty ? `<div class="uncertainty">? ${linkify(turn.uncertainty)}</div>` : ''}
      ${(turn.evidence || []).length ? `<div class="evidence">${evidenceHTML(turn.evidence)}</div>` : ''}
      <div data-trace>${renderTrace(turn.trace, { turnIndex: turn.turn_index })}</div>
    </div>`;
  }

  async send() {
    const question = this.questionEl.value.trim();
    if (!question || this.busy || this.blocked) return;

    let body;
    try { body = { question, ...this.picker.request() }; }
    catch (err) { return alert(err.message); }
    if (this.video && this.usePosEl.checked) body.player_time_sec = Number(this.video.currentTime.toFixed(2));

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
