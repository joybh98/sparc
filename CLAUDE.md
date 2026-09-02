# CLAUDE.md

This file orients Claude Code on this repo. Read it first, then `docs/` for deeper
background before making changes.

## What this project is

A barebones review tool to probe **how a frontier multimodal model reads a cataract
surgery clip versus how a human reviewer reads it** — before investing in a much larger
agentic pipeline (see `docs/BROADER_ARCHITECTURE.md`). The reviewer uploads a clip, the
model returns a structured step timeline (ordered steps with estimated start/end times
and per-step comments), the reviewer marks their own steps on the same clip, and the two
are compared side by side. Free-form Q&A about the clip runs alongside.

It originally compared model answers to a hand-written *canonical* reference per curated
clip (agree/disagree verdicts, a scorecard). That was descoped — see `docs/DECISIONS.md`
— in favor of the upload + model-vs-reviewer step comparison above.

This is one piece of a larger effort ("SPARC") led by Joy Bhalla (JHU CS grad student,
working with Prof. Vedula) building an agentic AI architecture for expert review of
cataract surgery video. This repo is intentionally a small, self-contained slice of
that larger system — it does not implement the full pipeline, and should not grow into
it in place. If asked to add JEPA encoders, MedCPT retrieval, an orchestrator/synthesizer
loop, or self-hosted model routing, treat that as a signal to check with the user first —
it likely belongs in the separate, larger SPARC repo, not bolted onto this barebones app.

## Current state (as of last session)

Fully working end-to-end, verified with a synthetic test clip and mock model responses
(no real API keys were available in that session). Not yet tested against real API keys
or real surgical footage.

- `app.py` — FastAPI backend. `POST /api/upload` stores an uploaded clip at
  `sessions/<id>/video.mp4`; `POST /api/query` is the single model-calling endpoint with
  `mode: "chat" | "steps"`; `POST /api/mark` records a reviewer step; `GET
  /api/sessions/<id>` / `GET /api/sessions/<id>/video` read them back
- `providers.py` — model router. `PROVIDERS` maps a model key (`gpt`/`claude`/`gemini`/
  `qwen`) to `{provider, key_env, model_env, default_model, generate, base_url?,
  base_url_env?}`; one `_<provider>_generate(system, prompt, images_b64, history,
  model_name, want_json, api_key, base_url=None)` adapter per SDK (qwen reuses
  `_openai_generate` against DashScope's OpenAI-compatible endpoint via `base_url`).
  `query_model()` (chat) and `query_steps()` (structured step timeline, JSON) share that
  path and each fall back to a clearly-labeled mock when the selected model's key env
  var is unset. `available_models()` reports the keys + whether each has a key set
- `frames.py` — samples N evenly-spaced frames from a local video via OpenCV:
  `extract_frames_b64()` (base64 JPEGs) for chat, `extract_frames_timed()` (+ timestamps)
  and `video_duration()` for step analysis
- `static/index.html` — single-page vanilla JS UI: upload panel + video player + model
  dropdown (populated from `GET /api/models`, applies to both Analyze and Q&A),
  "Model's read" (step-hint input, Analyze button, model step table + assessment),
  reviewer
  step-marking form with "from playhead" helpers, model-vs-reviewer comparison table
  (client-side IoU: match / partial / model-only / you-only), Q&A box + response card +
  turn history
- `sample_clips/metadata.json` — retained on disk (hand-authored capsulorhexis /
  phaco reference text) but no longer referenced by any code
- `sessions/` — one `<id>.json` per session (`video`, `turns` with `type` chat|steps,
  `marks`) plus a `<id>/` dir holding the uploaded `video.mp4`
- `README.md` — setup + run instructions, API reference

See `docs/DECISIONS.md` for why things are shaped this way, and `docs/NEXT_STEPS.md` for
what was scoped as a nice-to-have but not yet built.

## Architecture rules to preserve

- **One model-calling endpoint.** `POST /api/query` is the single place that calls
  models — `mode="chat"` for Q&A, `mode="steps"` for the structured step timeline. Don't
  add per-model or per-task endpoints (`/api/query/gpt`, `/api/analyze`, etc.) — branch
  on `mode` and add to the `models` list instead. This was an explicit requirement.
  (`/api/upload` and `/api/mark` don't call models.)
- **Mock-first, key-optional.** Every provider function in `providers.py` must keep
  working with zero API keys set, via the mock fallback in `query_model()` and
  `query_steps()`. Don't remove or bypass this — it's what makes the app testable
  without spending API credits or requiring the user to have all three providers
  configured. Test with API keys unset — the mock path exists so the app is fully
  exercisable end-to-end that way.
- **Frames, not raw video, to the models.** Video is sampled into N JPEG frames
  (`frames.py`) before being sent to any model API. This was a deliberate barebones
  simplification (see `docs/DECISIONS.md`) — don't silently switch to native video
  upload for one provider without updating the others / the docs.
- **Model-vs-reviewer comparison is client-side.** The step comparison table in
  `static/index.html` is pure time-range math (IoU) over the model's `steps` and the
  reviewer's `marks` — no model call, no endpoint. Keep it that way.
- **Sessions are the gold-label seed.** Every turn (chat or steps) and mark gets logged
  to `sessions/<id>.json`. Keep this format append-friendly and stable — it's meant to
  be reusable as training/eval data later, per the broader project's gold-label problem.
- **No code comments**, per the user's standing preference — write self-explanatory code
  instead.

## Conventions

- Python: FastAPI + Pydantic request models, plain functions (no classes beyond
  `ModelResponse`), `python-dotenv` for config. No test suite exists yet — see
  `docs/NEXT_STEPS.md`.
- Frontend: single `static/index.html`, no build step, no framework. Keep it that way
  unless the user asks for something heavier.
- Model keys read from env vars: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `GOOGLE_API_KEY`, `DASHSCOPE_API_KEY` (Qwen), with model *names* configurable
  separately via `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `GOOGLE_MODEL`, `QWEN_MODEL` and the
  Qwen host via `QWEN_BASE_URL` (see `.env.example`) — this is deliberate, since frontier
  model version strings change frequently and shouldn't require code edits.
- Adding a provider = one `PROVIDERS` entry (+ a `_<provider>_generate` adapter, unless
  it speaks the OpenAI-compatible protocol, in which case reuse `_openai_generate` with
  a `base_url`); the UI dropdown and both query paths pick it up automatically. The UI
  passes a single selected model key in `models: [...]`; `query_steps` uses `models[0]`,
  `query_model` still iterates the list. No adapter has had a successful live call yet —
  they're written from SDK docs; routing (mock + real-endpoint dispatch, confirmed with
  bogus keys hitting each provider's own 401) is verified, live request/response shapes
  are not.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in whichever API keys you have; any left blank = mock mode
uvicorn app:app --reload --port 8000
```

Then open http://localhost:8000, upload a clip, and use the panels top to bottom. Full
API reference is in `README.md`.
