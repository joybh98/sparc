/* VideoPanel: the UI for sessions with a video. The analysis agent starts as soon as the
   mounted panel exists, streaming its trace live; afterwards the reviewer can chat with
   the model (follow-ups) and mark their own steps. */

const VideoPanel = {
  modelSteps: [],
  userMarks: [],
  running: false,

  mount(root, chat, upload) {
    this.modelSteps = [];
    this.userMarks = [];
    root.innerHTML = `<div class="layout">
      <div>
        <div class="panel">
          <h3>Clip</h3>
          <small class="hint">session ${esc(App.sessionId.slice(0, 8))} · ${esc(upload.duration_sec)}s</small>
          <video id="clipVideo" controls></video>
        </div>
        <div class="panel">
          <h3>Mark your own steps</h3>
          <input id="markLabel" type="text" placeholder="step label (e.g. Capsulorhexis)" />
          <div class="row" style="margin-top:6px;">
            <input id="markStart" type="text" placeholder="start sec" />
            <input id="markEnd" type="text" placeholder="end sec" />
          </div>
          <div class="row" style="margin-top:6px;">
            <button class="secondary" id="markFromStart">◀ start from playhead</button>
            <button class="secondary" id="markFromEnd">◀ end from playhead</button>
          </div>
          <input id="markNote" type="text" placeholder="optional note" style="margin-top:6px;" />
          <button class="secondary" style="margin-top:8px;" id="addMark">Add mark</button>
          <div id="marksList" style="margin-top:10px;"></div>
        </div>
      </div>
      <div>
        <div class="panel">
          <h3>Model's read</h3>
          <div class="status-line"><small id="stepsHint" class="hint"></small></div>
          <div id="stepsTrace"></div>
          <div id="modelStepsBox"></div>
          <div id="assessmentBox"></div>
          <details class="cfg" style="margin-top:10px;" id="rerunBox">
            <summary>Re-run with a different agent or config</summary>
            <div id="analysisPicker" style="margin-top:6px;"></div>
            <input id="stepHint" type="text" placeholder="optional: what you expect to see in this clip" style="margin-top:6px;" />
            <button style="margin-top:8px;" id="rerunBtn">Re-run analysis</button>
          </details>
        </div>
        <div class="panel">
          <h3>Model steps vs. your steps</h3>
          <div id="comparisonBox"></div>
        </div>
        <div id="chatSlot"></div>
      </div>
    </div>`;

    $('clipVideo').src = upload.video_url + '?t=' + Date.now();
    this.picker = new AgentPicker($('analysisPicker'), { filter: a => a.kind === 'steps' });
    this.picker.populate(App.modality('video'));
    $('chatSlot').appendChild(chat.root);
    chat.setModality(App.modality('video'), {
      title: 'Talk to the model',
      placeholder: 'e.g. Why did you end phacoemulsification there? Look closer at the incision.',
      suggestions: [
        'Which step are you least certain about?',
        'Where do you disagree with my marks?',
        'Why did you place the step boundaries where you did?',
        'Look closer at the incision.',
      ],
    });
    this.chat = chat;

    $('markFromStart').addEventListener('click', () => $('markStart').value = $('clipVideo').currentTime.toFixed(1));
    $('markFromEnd').addEventListener('click', () => $('markEnd').value = $('clipVideo').currentTime.toFixed(1));
    $('addMark').addEventListener('click', () => this.addMark());
    $('rerunBtn').addEventListener('click', () => this.analyze());
    this.renderComparison();

    this.analyze();     // the clip is read: start the agent right away
  },

  async analyze() {
    if (this.running) return;
    let body;
    try { body = { step_hint: $('stepHint').value.trim() || null, ...this.picker.request() }; }
    catch (err) { return this.setHint(err.message, true); }

    this.running = true;
    $('rerunBtn').disabled = true;
    this.chat.setBlocked(true, 'analysis is running…');
    this.setHint('agent running…');
    $('stepsTrace').innerHTML = '';
    $('modelStepsBox').innerHTML = '';
    $('assessmentBox').innerHTML = '';
    try {
      const turn = await runAgent(body, ev => setTrace($('stepsTrace'), ev, { open: true, live: true }));
      setTrace($('stepsTrace'), turn.trace, { open: true });
      this.setHint(`${turn.model} · ${turn.provider} · ${turn.latency_ms}ms · ${turn.iterations} iteration(s)`
        + (turn.error ? ` · ${turn.error}` : ''), !!turn.error);
      this.modelSteps = (turn.model_steps || []).map(s => ({
        step_label: s.step_label || '(unlabeled)',
        start_sec: Number(s.start_sec) || 0,
        end_sec: Number(s.end_sec) || 0,
        comment: s.comment || '',
      }));
      this.renderModelSteps(turn.assessment);
      this.renderComparison();
      this.chat.showChips(this.modelSteps.length > 0);
    } catch (err) {
      this.setHint(err.message, true);
    } finally {
      this.running = false;
      $('rerunBtn').disabled = false;
      this.chat.setBlocked(false);
    }
  },

  setHint(text, isError = false) {
    $('stepsHint').textContent = text;
    $('stepsHint').className = 'hint' + (isError ? ' error' : '');
  },

  renderModelSteps(assessment) {
    const box = $('modelStepsBox');
    box.innerHTML = !this.modelSteps.length ? '<small class="hint">No steps returned.</small>' : `<table>
      <tr><th>Step</th><th>Start</th><th>End</th><th>Comment</th></tr>
      ${this.modelSteps.map(s => `<tr>
        <td>${esc(s.step_label)}</td>
        <td><a href="#" data-seek="${esc(s.start_sec)}">${fmt(s.start_sec)}</a></td>
        <td><a href="#" data-seek="${esc(s.end_sec)}">${fmt(s.end_sec)}</a></td>
        <td>${esc(s.comment)}</td></tr>`).join('')}</table>`;
    $('assessmentBox').innerHTML =
      assessment ? `<div class="assessment"><strong>Assessment:</strong> ${esc(assessment)}</div>` : '';
  },

  async addMark() {
    const step_label = $('markLabel').value.trim();
    const start_sec = parseFloat($('markStart').value);
    const end_sec = parseFloat($('markEnd').value);
    const note = $('markNote').value.trim();
    if (!step_label || isNaN(start_sec) || isNaN(end_sec)) return alert('fill in label, start, end');

    const data = await postJSON('/api/mark', { session_id: App.sessionId, step_label, start_sec, end_sec, note });
    this.userMarks = data.marks || [];
    $('marksList').innerHTML = this.userMarks.map(m =>
      `<div class="mark-row"><span>${esc(m.step_label)}</span><span>${fmt(m.start_sec)}–${fmt(m.end_sec)}</span></div>`
    ).join('');
    this.renderComparison();
    $('markLabel').value = '';
    $('markNote').value = '';
  },

  iou(a, b) {
    const inter = Math.max(0, Math.min(a.end_sec, b.end_sec) - Math.max(a.start_sec, b.start_sec));
    const union = (a.end_sec - a.start_sec) + (b.end_sec - b.start_sec) - inter;
    return union > 0 ? inter / union : 0;
  },

  renderComparison() {
    const box = $('comparisonBox');
    if (!this.modelSteps.length || !this.userMarks.length) {
      box.innerHTML = '<small class="hint">Once the analysis finishes, add at least one mark to compare.</small>';
      return;
    }
    const used = new Set();
    const rows = this.modelSteps.map(ms => {
      let best = -1, bestScore = 0;
      this.userMarks.forEach((um, i) => {
        const s = this.iou(ms, um);
        if (s > bestScore) { bestScore = s; best = i; }
      });
      let verdict = 'model-only', um = null;
      if (best >= 0 && bestScore > 0) {
        um = this.userMarks[best];
        used.add(best);
        verdict = bestScore >= 0.5 ? 'match' : 'partial';
      }
      return { ms, um, bestScore, verdict };
    });
    this.userMarks.forEach((um, i) => {
      if (!used.has(i)) rows.push({ ms: null, um, bestScore: 0, verdict: 'you-only' });
    });
    const cell = s => s ? `${esc(s.step_label)}<br><small class="hint">${fmt(s.start_sec)}–${fmt(s.end_sec)}</small>` : '—';
    box.innerHTML = `<table>
      <tr><th>Model step</th><th>Your step</th><th>Overlap (IoU)</th><th>Verdict</th></tr>
      ${rows.map(r => `<tr><td>${cell(r.ms)}</td><td>${cell(r.um)}</td>
        <td>${(r.bestScore * 100).toFixed(0)}%</td>
        <td><span class="tag ${r.verdict}">${r.verdict.replace('-', ' ')}</span></td></tr>`).join('')}</table>`;
  },
};
