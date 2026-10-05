# Design decisions — barebones eval app

Captures the choices made in the chat that produced this repo, and why, so they don't
get silently relitigated by a future session.

## 2026-10-02 — configurable agent loop with traces (`mode="agent"`)

- **What:** a small generic engine (`core/`) plus per-modality packs (`modalities/video/`).
  A loop calls the model with tools, runs the tool calls, feeds results back, and records
  a trace. Agents are JSON config (prompt, tool whitelist, stop condition, iteration cap).
- **Why config + registry:** "plug and play" = adding a tool, an agent, or a modality
  never touches the engine. Configs can only reference registered tools (no arbitrary
  code), and are validated on load with readable errors.
- **Single endpoint preserved:** agent runs are a `mode` branch on `/api/query`. Legacy
  `chat`/`steps` modes are unchanged (only the `models` default moved from `["gpt"]` to
  `[]` so an agent's own `model` can apply; legacy modes still fall back to `gpt`).
- **Trace stores refs, not bytes:** tool images become small refs (`{time_sec, zoom,...}`)
  re-rendered by `GET /api/sessions/<id>/frame`, so session JSON stays small.
- **Routing:** the modality is chosen by the session: video uploaded -> `video`, otherwise
  the `text` fallback (free-form chat). Uploading mid-chat switches the session and the
  earlier chat is replayed as memory.
- **Follow-ups** are agent turns (`type: "followup"`, `parent_turn`) with prior Q&A
  replayed as memory and the earlier analysis/marks available as tools.
- **Live trace via polling:** agent runs execute in a background thread and the UI polls
  `GET /api/runs/<id>?after=<seq>` (chosen over SSE: plain fetch, no connection state).
  Runs are in-memory (1h TTL); the saved turn in the session file is the durable record.
  Turns are saved under a per-session lock so a chat and an analysis can finish together.
- **UI flow:** upload auto-starts the analysis agent; the single-call (no tools) options
  were removed from the UI. The legacy `chat`/`steps` modes remain in the API only.
- **Not verified live:** the OpenAI/Qwen, Anthropic and Gemini tool-calling adapters are
  covered by translation/parsing tests against fake SDK clients, not by real API calls.

## 2026-08-31 — pivot from canonical scoring to upload + model-vs-reviewer steps

The original design (curated demo clips, a hand-written `canonical` answer per clip,
agree/disagree verdicts, a per-model scorecard) was descoped at the user's direction.
The app is now: **upload your own clip → model returns a structured step timeline →
mark your own steps → compare**, with free-form Q&A alongside.

- **Why:** the interesting comparison is the model's segmentation/read of a clip vs. a
  reviewer's, on the reviewer's own footage — not proximity to a fixed canonical
  paragraph. Curated clips + canonical text were friction without payoff at this stage.
- **Removed:** clip picker + `GET /api/clips*`, canonical panel, `POST /api/verdict`,
  `GET /api/scorecard`, `sample_clips/metadata.json` wiring (file kept on disk — it
  holds hand-authored reference text and this isn't a git repo).
- **Added:** `POST /api/upload` (multipart, video stored at `sessions/<id>/video.mp4`),
  `GET /api/sessions/<id>/video`, `mode="steps"` on `/api/query` →
  `providers.query_steps()` asking for JSON `{steps:[{step_label,start_sec,end_sec,
  comment}], assessment}` from timestamped frames, and a client-side IoU comparison
  table (match / partial / model-only / you-only).
- **Single model-calling endpoint preserved:** step analysis is a `mode` branch on
  `/api/query`, not a new endpoint. The comparison is client-side math, no endpoint.
- **Frames-not-video preserved:** step analysis still sends sampled JPEG frames, now
  each tagged with its timestamp (`frames.extract_frames_timed`).
- **Deferred:** drawing boxes/arrows on frames (was briefly in scope, pulled out to
  keep this pass focused on step segmentation) — see `docs/NEXT_STEPS.md`.

## Scope-setting question

The original ask: build a barebones application that (1) calls models, (2) through a
single API endpoint, (3) can select or retrieve a video, (4) lets the user mark steps,
(5) and runs a bidirectional feedback-loop conversation — with the end goal of
understanding where frontier models stand relative to the canonical version, for
cataract surgery.

Before building, a features brainstorm was done first (see "Nice-to-haves considered"
below), then three scoping questions were asked and answered:

| Question | Answer chosen |
| --- | --- |
| Which frontier models to call | GPT-5.4, Claude, Gemini (cloud APIs) |
| Where videos come from | Local sample clips the user provides/uploads |
| What "canonical" is compared against | A hand-written reference answer per clip |

These three answers shape most of the architecture below. Revisit them explicitly if
the project's constraints change (e.g. moving off local clips to the real Blob Storage
pipeline, or switching the canonical source to the text-extraction pipeline).

## Key architectural choices

**Single endpoint, multi-model fan-out.** `POST /api/query` takes a `models: []` list
and calls each synchronously, returning all responses together. Rejected alternative:
one endpoint per provider — this violates the explicit "single API endpoint"
requirement and also makes model comparison (the whole point of the app) awkward for
the frontend to assemble.

**Frames over native video upload.** Some providers (e.g. Gemini) support native video
upload; OpenAI and Anthropic's mainstream chat APIs are frame/image-based. Rather than
special-case one provider's richer video support, the app uniformly extracts N sampled
frames (`frames.py`, OpenCV) and sends the same frame set to all three, so comparisons
are apples-to-apples. Trade-off: no model gets to use audio, native temporal encoding,
or full frame-rate — acceptable for a barebones probe, worth revisiting if a provider's
native video handling turns out to matter a lot for surgical motion assessment.

**Mock provider fallback instead of requiring all API keys upfront.** The session that
built this had zero API keys available (verified via `env | grep`). Rather than block
on that, every provider call falls back to a clearly-labeled mock response
(`(mock — no OPENAI_API_KEY set)`) when its key is absent, so the full request/response/
logging/scorecard pipeline could be built and verified end-to-end without live keys.
This is also just generally useful for local dev / demoing without burning API credits.

**Hand-written canonical answers, not derived ones.** The full SPARC architecture has an
entire text pipeline (extraction → clustering → expert ratification) for producing
canonical answers. Standing that up was explicitly out of scope for this barebones app;
`sample_clips/metadata.json` instead has manually authored reference answers per clip
per the scoping answer above. If/when the real canonical pipeline exists, the natural
integration point is swapping how `metadata.json`'s `canonical` block gets populated —
the rest of the app shouldn't need to change.

**Bidirectional feedback loop, implemented as:** (1) conversation history is threaded
through every `/api/query` call so follow-ups have context, (2) the system prompt
explicitly permits a model to ask a clarifying question back rather than guess, and
(3) the reviewer's agree/disagree/unsure verdicts are captured per model per turn and
persisted — so the "loop" is both conversational (model can ask back) and evaluative
(human corrects/judges, and that judgment is logged as data, mirroring the
AI-Raised-Questions pattern from the broader SPARC canonical-version design).

**Sessions as a lightweight gold-label seed.** Every session file (`sessions/*.json`)
logs turns, marks, and verdicts. This wasn't purely for debugging — the broader SPARC
architecture doc lists "gold-label mechanism" as an explicitly open problem, and this
app's session logs are a plausible (if small-scale) answer to that, worth mentioning if
this work comes up in the professor call context.

## Nice-to-haves considered (brainstormed before building; partially implemented)

Presented to the user before scoping, grouped by theme:

- Grounding & disagreement surfacing — citations per answer, confidence labels, visual
  diff between model step-marking and canonical, cross-model disagreement highlighting
- True bidirectional turns — model can ask clarifying questions, user can correct
  mid-conversation
- Gold-label/ratification path — corrections get written out as structured records
- Per-model scorecard — agreement rate, confidence calibration (ties to the user's own
  separate resume project on Gamified Confidence Elicitation / ECE / Brier score)
- Session logging/replay — save and reopen sessions, export as JSON
- Model routing abstraction — swap models via config, track latency/cost per call
- Baseline comparison slot — show the project's own JEPA step-head prediction alongside
  frontier models, not just model-vs-model

Implemented in this build: citations-via-prompt (models are instructed to cite which
part of the frame sequence they rely on, not structurally enforced), confidence labels
(via prompt, not structured output), clarifying-question capability, verdict-based
gold-label path, scorecard endpoint, session logging/replay, model routing abstraction
via `providers.py`. **Not implemented** — see `docs/NEXT_STEPS.md`.


## 2026-10-02 — step evidence/confidence/uncertainty + queryable trajectory with per-call marks

- **Step schema** gains required `confidence`, `evidence` (`[{time_sec, note}]`) and optional
  `uncertainty`, enforced in `submit_steps` validation (bad values are returned to the agent
  as an error so it retries) rather than only in the prompt. Evidence is time-based, matching
  the timestamped frames and `sample_frames`/`zoom_frame`.
- **Trajectory** keeps the full trace in the session JSON and adds a flat SQLite copy
  (`core/trajectory.py`, `sessions/trajectory.db`) written after each turn: run -> model_call
  -> tool, so "which call invoked what" and the reviewer's per-call verdict are queryable.
  Marking is `POST /api/calls/mark`; it calls no model, so the single query endpoint stands.
- **Not done:** evidence/confidence for follow-up answers (free text), and verdicts on a
  step's confidence or evidence individually (marks are per tool call).


## 2026-10-05 — grounded answers: evidence on answers, clickable timestamps, player context

- **Answers are structured.** The video `followup` agent now ends by calling `submit_answer`
  (`answer`, `confidence`, `uncertainty`, `evidence`) instead of replying in free text, so
  "the frames it relied on" is explicit rather than inferred from which frames it fetched.
  It shares its validator and evidence shape with `submit_steps`. `turn.answer` still holds
  the text, so chat memory and old sessions are unchanged. A model that skips the tool is
  nudged once, then its text is used.
- **Timestamps are linked client-side** (`linkify` in `common.js`), not by the server, so
  nothing about stored answers changes. Accepted limitation: a bare duration such as "3 s"
  also becomes a link.
- **Player position is sent as `player_time_sec`** on `/api/query` (no new endpoint). The
  video modality adds a line to the message and attaches that frame, rather than leaving the
  model to fetch it, because the point is that "what's happening here?" works on the first
  call. The UI shows what will be sent and lets the reviewer turn it off.
- **Flat `evidence` table** in `trajectory.db` (one row per cited frame, plus a NULL-time row
  when nothing is cited), joined to `calls` through the delivering call. Chosen over leaving
  evidence only inside JSON columns so "evidence behind calls I marked incorrect" is a plain
  join. Reviewer clicks and scrubs are deliberately not logged.
