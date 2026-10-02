# SPARC Frontier-Model Eval (barebones)

A minimal app for probing how a frontier multimodal model reads a cataract surgery clip
versus how a human reviewer reads it. Pick the model (ChatGPT / Claude / Gemini / Qwen)
from a dropdown; whichever you have no API key for runs on a labeled mock.

## What it does

Two kinds of session, chosen by whether a clip has been uploaded:

- **Text chat** (no video yet): talk to the model; it can search earlier messages, do
  arithmetic, or ask you a clarifying question.
- **Video** (after upload): the moment the clip is read, an **analysis agent starts
  automatically** and its trace streams into the page live (each model call, tool call,
  result and frame thumbnail), ending in a structured step timeline plus an assessment.
  Then you can:
  - **talk to the model** (follow-ups): it can re-read its own analysis, your marks and
    the earlier chat, and look at the clip again on demand;
  - **mark your own steps**: scrub the player, fill start/end ("from playhead"
    helpers), saved via `POST /api/mark`;
  - **compare**: a client-side table aligns each model step to your closest mark by
    temporal IoU (match / partial / model-only / you-only).

Chat from before the upload carries over into the video session.

Any model you *don't* have a key for automatically falls back to a mock response
(clearly labeled `(mock — no <KEY> set)`) so the app is fully testable end-to-end
without any keys at all.

## Agents (configurable tool-using loops)

`POST /api/query` with `mode: "agent"` runs a tool-using loop and returns the full
**trace** (model calls, tool calls, tool results, final) on the turn. Everything is
organized by *modality*; a session with an uploaded clip uses `video`.

- **Agents are JSON files**: `modalities/<modality>/agents/*.json`. Fields: `name`,
  `system_prompt`, `tools` (whitelist), `kind` (`steps` fills `model_steps`/`assessment`
  like the legacy mode; `followup` is a Q&A turn), `stop_when` (`no_tool_calls` or
  `tool:<name>`), `max_iterations`, `initial_frames`, `required_output`, `mock`, `model`.
  Files are re-read on every request, so a new agent needs no restart. Typos, unknown
  tools and out-of-range values are reported by `GET /api/agents` under `invalid`.
- **Or send one inline**: `agent_config: {...}` in the query body (validated the same way,
  and snapshotted onto the turn).
- **Add a tool**: one decorated function in `modalities/<modality>/tools.py`:
  `@tool("video", "name", "description", {json schema})`, returning a `ToolResult`.
  Configs can only name registered tools, so they can't run arbitrary code.
- **Add a modality**: a package under `modalities/` exposing `MODALITY` (see
  `modalities/video/__init__.py`). It is auto-discovered; a broken one is skipped and
  reported rather than breaking the app.
- **In the UI**: the chat and the "re-run analysis" box each have an agent picker and an
  editable config (edit it and the edited JSON runs as an inline `agent_config`). Every
  run shows a collapsible trace: model reasoning, each tool call and result, frame
  thumbnails (click to seek the player), latency and stop reason.
- **Live traces**: the UI sends `stream: true`; the server returns a `run_id` right away,
  runs the loop in a background thread, and `GET /api/runs/<id>?after=<seq>` returns new
  trace events plus the saved turn when finished.
- **Text modality**: `modalities/text/` (tools `search_history`, `calculate`, `ask_user`;
  agent `chat`). Used for any session without a video.
- **Follow-ups**: after a step analysis, `agent: "followup"` + `question` answers using
  the earlier analysis, the reviewer's marks, prior Q&A, and on-demand frame tools.

With no API key for the chosen model, a scripted mock runs the same loop. Run the tests
(all offline, mock-only): `python -m unittest discover -s tests -t .`

## Setup

```bash
cd sparc-eval-app
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add whichever API keys you have to `.env`:

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIzaSy...          # Gemini Developer API key from aistudio.google.com/apikey
DASHSCOPE_API_KEY=sk-...          # Qwen, from Alibaba Cloud Model Studio (bailian) — has a free trial quota
```

Model *names* are configurable separately (`OPENAI_MODEL`, `ANTHROPIC_MODEL`,
`GOOGLE_MODEL`, `QWEN_MODEL`) so version-string changes don't need code edits. Qwen goes
through DashScope's OpenAI-compatible endpoint; `QWEN_BASE_URL` defaults to the
international host — set it to `https://dashscope.aliyuncs.com/compatible-mode/v1` if your
DashScope account is in the mainland-China region.

## Run

```bash
uvicorn app:app --reload --port 8000
```

Open http://localhost:8000. Chat right away, or upload a clip and watch the analysis run;
then talk to the model and mark your own steps.

## Where this fits the bigger architecture

This is deliberately a much smaller slice than the full agentic design (no Source
Adapter, no MedCPT skill matcher, no orchestrator/synthesizer split, no self-hosted
routing). It answers a narrow question first: given raw sampled frames, how does an
off-the-shelf frontier model's step segmentation and read of a clip compare to a
reviewer's, before investing in the full pipeline. The session JSON files in `sessions/`
double as a rough gold-label seed. See `docs/DECISIONS.md` for the history (this app
previously scored models against hand-written canonical answers).

## API

- `GET /api/models` — model keys (`gpt`/`claude`/`gemini`/`qwen`), resolved model name,
  and whether each has an API key set
- `POST /api/upload` — multipart `file` (+ optional `session_id` form field); stores the
  clip, returns `{session_id, video_url, duration_sec}`
- `GET /api/sessions/{session_id}/video` — stream the uploaded clip
- `POST /api/query` — `{session_id, mode, models: ["gpt"], conversation_history?, n_frames?}`
  - `models` — one key from `/api/models`; `mode:"steps"` uses the first, `mode:"chat"` fans out over all
  - `mode: "chat"` (default) — also needs `question`; returns a chat turn
  - `mode: "steps"` — also takes `step_hint?`; returns a turn with `model_steps` + `assessment`
- `POST /api/mark` — `{session_id, step_label, start_sec, end_sec, note?}`
- `GET /api/sessions/{session_id}` — full session transcript (`video`, `turns`, `marks`)
