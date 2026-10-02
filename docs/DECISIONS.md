# Design decisions — barebones eval app

Captures the choices made in the chat that produced this repo, and why, so they don't
get silently relitigated by a future session.

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


## 2026-10-02 — evidence/confidence/uncertainty per step + call trajectory in SQLite

- **Step schema** gains `confidence` (0–1), `uncertainty` (text) and `evidence`
  (`[{frame, note}]`, normalized server-side to drop out-of-range frames and add
  `time_sec`). Enforced via the prompt and `_normalize_steps`, not provider JSON schemas.
- **Trajectory** is stored in SQLite (`sessions/trajectory.db`) rather than session JSON:
  one row per call with `parent_id`/`caller`, so "which call invoked what" is queryable and
  the reviewer's per-call verdict is an in-place update. Session turns keep only `trace_id`,
  so the session JSON format stays stable. CSV export is a one-line `sqlite3` command.
- **Marking** a call is `POST /api/calls/mark`; it does not call a model, so the
  single-model-endpoint rule is unaffected.
