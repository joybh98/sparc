/* TextPanel: the UI for sessions with no video. Free-form chat, plus the upload box
   that switches the session over to the video modality. */

const TextPanel = {
  mount(root, chat) {
    root.innerHTML = `<div class="single">
      <div class="panel">
        <h3>Upload a surgical clip</h3>
        <input type="file" id="videoFile" accept="video/*" />
        <button id="uploadBtn" style="margin-top:8px;">Upload &amp; analyze</button>
        <small class="hint" id="uploadHint" style="display:block;margin-top:6px;">
          As soon as the clip is read, an agent starts analyzing it and you can watch its trace.
          Or just chat below.</small>
      </div>
      <div id="chatSlot"></div>
    </div>`;
    $('chatSlot').appendChild(chat.root);
    chat.setModality(App.modality('text'), {
      title: 'Chat',
      placeholder: 'Ask anything. Upload a clip above to have it analyzed.',
    });
    chat.setPlayer(null);
    chat.setBlocked(false);
    $('uploadBtn').addEventListener('click', () => App.uploadVideo($('videoFile'), $('uploadHint'), $('uploadBtn')));
  },
};
