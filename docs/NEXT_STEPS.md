# Next steps / not yet built

Things scoped as nice-to-have or explicitly deferred, in rough priority order for
picking this back up.

## Not yet done

- **Real API key testing.** Everything so far has only been verified against the mock
  provider path (no OpenAI/Anthropic/Google keys were available in the session that
  built this). First real task: add keys to `.env` and confirm `call_openai`,
  `call_openai_steps`, and the anthropic/google equivalents in `providers.py` actually
  work against live APIs — message formats for multi-image + conversation history +
  JSON-mode step output are written from API docs knowledge, not tested against a live
  response.
- **Real video clips.** Only a synthetic OpenCV-generated test clip has been run
  through upload + frame extraction. No real cataract surgery footage yet.
- **Frame-drawing annotations.** Deferred in the 2026-08-31 pivot: let the reviewer draw
  boxes/arrows/freehand on a frame (canvas overlay on the `<video>`), capture the
  composited still, and send those annotated frames + notes into `/api/query`.
- **Structured step-output schema enforcement.** `query_steps()` asks for a JSON shape
  and `json.loads`es it, but nothing validates the fields or clamps times to
  `[0, duration]`. A Pydantic model / per-provider JSON schema would harden this.
- **Persist & reload comparison state in the UI.** `modelSteps` and `userMarks` live
  only in browser memory; reopening a session (`GET /api/sessions/<id>`) doesn't
  rehydrate the step table or comparison. Backend already stores everything.
- **Old session-video cleanup.** `sessions/<id>/video.mp4` files accumulate with no
  eviction. Needs a retention policy or a cleanup command.
- **Latency/cost tracking rollup.** Per-call latency is captured (`latency_ms` in each
  response) but not aggregated anywhere; no cost tracking at all yet.
- **Confidence calibration (ECE/Brier).** Flagged in the broader SPARC design doc as
  the weakest/least-validated part of the larger system too, and explicitly named as
  connecting to the user's own separate resume project on Gamified Confidence
  Elicitation. Not started here.
- **Baseline comparison slot for the project's own JEPA step-head model.** Would let
  the app show "frontier model vs. canonical vs. our own trained model" three-way,
  which is arguably the most interesting comparison long-term. Stubbed conceptually,
  not implemented.
- **Auth / multi-user.** Currently single-user, no auth at all — fine for a personal
  barebones probe, would need addressing before sharing with e.g. Prof. Vedula's team.
- **Automated tests.** No test suite exists. `frames.py` and `providers.py`'s mock path
  are the easiest first candidates (pure functions, no live API dependency).

## Explicitly out of scope for this repo (belongs in the larger SPARC system instead)

- JEPA video encoder, Q-Former adapters, skill-assessment heads
- MedCPT-based skill matching / retrieval
- Orchestrator/synthesizer agentic loop, `max_turns` handling, `termination_reason`
- Blob Storage / Event Grid / Service Bus ingestion pipeline
- Text extraction / clustering / expert ratification for canonical answers
- Self-hosted model routing for `jhu_restricted` data scope

If any of these come up as a request in a future session working on this repo, flag
that it likely belongs in the separate larger SPARC architecture repo, not here.
