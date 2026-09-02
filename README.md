# SPARC Frontier-Model Eval (barebones)

A minimal app for probing how a frontier multimodal model reads a cataract surgery clip
versus how a human reviewer reads it. Pick the model (ChatGPT / Claude / Gemini / Qwen)
from a dropdown; whichever you have no API key for runs on a labeled mock.

## What it does

- **Upload a clip** (`.mp4/.mov/.webm/.mkv`). It's stored per-session under
  `sessions/<id>/video.mp4`.
- **Model's read** — one endpoint (`POST /api/query` with `mode: "steps"`) samples N
  timestamped frames and asks the model for a structured step timeline: ordered steps
  with estimated `start_sec`/`end_sec` and a per-step comment, plus an overall
  assessment.
- **Mark your own steps** — scrub the player, fill start/end (there are "from playhead"
  helpers), label the step. Saved via `POST /api/mark`.
- **Compare** — a client-side table aligns each model step to your closest mark by
  temporal IoU and labels it match / partial / model-only / you-only.
- **Ask about the clip** — `POST /api/query` with `mode: "chat"` fans a free-form
  question + the sampled frames out to the selected model(s); conversation history is
  threaded so follow-ups have context, and the model may ask a clarifying question back.

Any model you *don't* have a key for automatically falls back to a mock response
(clearly labeled `(mock — no <KEY> set)`) so the app is fully testable end-to-end
without any keys at all.

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

Open http://localhost:8000 and work the panels top to bottom: upload → analyze → mark →
compare → ask.

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
