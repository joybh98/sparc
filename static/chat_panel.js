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

    this.threadEl.addEventListener('click', e => this.onFeedbackClick(e));
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

  /* ---- flagging an answer: 👍 / 👎 with a short reason and an optional correction ---- */

  feedbackHTML(n, editing = false) {
    const fb = App.feedback[n] || null;
    const btn = (kind, icon, tip) =>
      `<button class="fb-btn ${kind}${fb && fb.rating === kind ? ' active' : ''}" data-fb="${kind}" title="${tip}">${icon}</button>`;
    let note = '';
    if (fb && fb.rating === 'down' && !editing) {
      note = `<small class="hint">Flagged${fb.reason ? `: ${esc(fb.reason)}` : ''}${fb.correction ? ' · correction saved' : ''}
        · <a href="#" data-fb-edit>edit</a></small>`;
    } else if (fb && fb.rating === 'up') {
      note = '<small class="hint">Marked as right</small>';
    }
    const form = editing ? `<div class="fb-form">
      <input type="text" data-fb-reason maxlength="500" placeholder="What was wrong? (short reason)" value="${esc(fb && fb.reason || '')}" />
      <textarea rows="2" data-fb-correction placeholder="What should it have said? (optional; saved as the preferred answer)">${esc(fb && fb.correction || '')}</textarea>
      <div class="row"><button data-fb-save>Save flag</button><button class="secondary" data-fb-cancel>Cancel</button></div>
    </div>` : '';
    return `${btn('up', '👍', 'This answer was right')}${btn('down', '👎', 'Flag this answer as wrong')} ${note}${form}`;
  }

  async saveFeedback(box, rating, reason, correction) {
    const n = Number(box.dataset.fbTurn);
    const out = await postJSON('/api/answers/feedback', {
      session_id: App.sessionId, turn_index: n, rating, reason: reason || null, correction: correction || null });
    if (out.error) { alert(out.error); return; }
    if (out.feedback) App.feedback[n] = out.feedback; else delete App.feedback[n];
    box.innerHTML = this.feedbackHTML(n);
  }

  onFeedbackClick(e) {
    const box = e.target.closest('.fb');
    if (!box) return;
    const n = Number(box.dataset.fbTurn);
    const cur = App.feedback[n];
    const act = e.target.closest('[data-fb], [data-fb-save], [data-fb-cancel], [data-fb-edit]');
    if (!act) return;
    e.preventDefault();

    if (act.matches('[data-fb="up"]')) {
      this.saveFeedback(box, cur && cur.rating === 'up' ? null : 'up');
    } else if (act.matches('[data-fb="down"]')) {
      if (cur && cur.rating === 'down') this.saveFeedback(box, null);
      else box.innerHTML = this.feedbackHTML(n, true);
    } else if (act.matches('[data-fb-edit]')) {
      box.innerHTML = this.feedbackHTML(n, true);
    } else if (act.matches('[data-fb-cancel]')) {
      box.innerHTML = this.feedbackHTML(n);
    } else if (act.matches('[data-fb-save]')) {
      this.saveFeedback(box, 'down', box.querySelector('[data-fb-reason]').value.trim(),
                        box.querySelector('[data-fb-correction]').value.trim());
    }
  }

  card(turn, traceOpen) {
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
      ${turn.answer && !turn.error && turn.turn_index != null
        ? `<div class="fb" data-fb-turn="${turn.turn_index}">${this.feedbackHTML(turn.turn_index)}</div>` : ''}
      <div data-trace>${renderTrace(turn.trace, { turnIndex: turn.turn_index, open: traceOpen })}</div>
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
      const turn = await runAgent(body, ev => setTrace(traceHost, ev, { live: true }));
      cardEl.outerHTML = this.card(turn, traceOpen(traceHost));
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
