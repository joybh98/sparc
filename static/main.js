/* Boot + routing. The session starts as text chat; uploading a clip switches it to the
   video modality (the server routes by whether the session has a video). */

async function loadModels() {
  const data = await (await fetch('/api/models')).json();
  const sel = $('modelSelect');
  sel.innerHTML = data.models.map(m =>
    `<option value="${esc(m.key)}">${esc(m.key)} · ${esc(m.model_name)}${m.has_key ? '' : ' (mock — no key)'}</option>`
  ).join('');
  const live = data.models.find(m => m.has_key);
  if (live) sel.value = live.key;
}

App.uploadVideo = async function (fileInput, hintEl, button) {
  if (!fileInput.files.length) return alert('choose a video file first');
  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  fd.append('session_id', App.sessionId);
  hintEl.textContent = 'uploading…';
  button.disabled = true;
  try {
    const data = await (await fetch('/api/upload', { method: 'POST', body: fd })).json();
    if (data.error) { hintEl.textContent = data.error; return; }
    VideoPanel.mount($('root'), App.chat, data);
  } catch (err) {
    hintEl.textContent = 'upload failed: ' + err.message;
  } finally {
    button.disabled = false;
  }
};

(async function boot() {
  await loadModels();
  const data = await (await fetch('/api/agents')).json();
  App.modalities = data.modalities;
  if (data.errors && data.errors.length) console.warn('modality load errors', data.errors);
  App.chat = new ChatPanel();
  TextPanel.mount($('root'), App.chat);
})();
