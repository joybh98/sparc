# Broader project context: SPARC

This repo (the barebones frontier-model eval app) is a small offshoot of a much larger
project. This doc summarizes that larger context so a Claude Code session working on
this repo understands where it fits and doesn't accidentally try to rebuild the whole
thing. The authoritative, evolving version of this material lives in the user's
separate "SPARC" Claude project (docs: `chat.txt`,
`Consolidated_Design_Decisions.docx`), not in this repo — treat this file as a snapshot,
not the source of truth.

## Who / what

Joy Bhalla, JHU CS grad student, building an agentic AI architecture for expert review
of cataract surgery video (capsulorhexis focus), working with Prof. Vedula. Inspired by
MedClaw and SurgVidLM papers. Being prepared for presentation to Prof. Vedula and an
NLP-researcher colleague.

## The larger system has two halves that converge

1. **Data collection vehicle** — video + annotation ingestion, JEPA-based step/skill/
   feedback inference.
2. **Canonical version system** — text extraction from surgical literature, taxonomy
   discovery via clustering, expert ratification, bidirectional AI interrogation.

Both share infrastructure: raw data (video, Excel annotations, an internal annotation
tool's JSON output, text sources) lands in Blob Storage, triggers an Event Grid →
Service Bus → Ingestion Pipeline chain, splits into a video/Excel path and a text path,
and both reconverge at a Structured Output layer.

## The agentic query architecture (from `chat.txt`, "finalized" as of last SPARC session)

Flow: expert retrieves an existing case or uploads video → Source Adapter formats to a
canonical clip (16-frame / 250×250 / 1fps, matching the JEPA encoder's training
preprocessing) → Canonical Clip Store. Separately, an expert query (text or
Whisper-transcribed audio) becomes query `q`. Both feed a duplicate check (exact match
on case_id+question against a Trajectory Log Store) before assembling a context object
`c0`: `{question, case_ref: {case_id, canonical_clip_pointer, fps, frame_count,
duration, data_scope}, session_id, trajectory: []}` — a pointer, never raw pixels.

- **Skill Matcher**: MedCPT embeds `q` once, compares against stored trigger
  embeddings, top-1 above threshold; no match leaves `c0` unchanged.
- **Orchestrator loop**: GPT-5.4, text-only (never sees video directly), native
  tool-calling, one action per turn. `max_turns` is a hard cap checked externally
  (code-enforced, not trusted to the model). `termination_reason` ("stop" vs
  "max_turns_reached") is passed downstream so forced cutoffs aren't presented as
  confident answers.
- **`data_scope` routing**: `public_domain` → GPT-5.4 / Claude Haiku 4.5 (cloud);
  `jhu_restricted` → DeepSeek V4 Flash/V3.2 (self-hosted, for privacy).
- **Tool suite m1–m7** (two backbones: Qwen3 for breadth, Llama 3.3 70B for
  precision/generation): m1 = JEPA encoder + step head; m2 = skill-assessment head +
  SAM-3 fusion (segmentation-derived geometry fused with JEPA embedding); m3 = Llama
  3.3 70B + Q-Former (feedback text); m4 = MedCPT RAG; m5 = Qwen3 + new Q-Former
  (whole-case, long-context — no training data exists yet, open gap); m6 = Llama 3.3
  70B + own Q-Former (crop/window, faithfulness-priority); m7 = Llama 3.3 70B + linear/
  MLP projection (single-frame select, trained from scratch since cross-attention
  degenerates at n=1).
- **Synthesizer**: Claude Haiku 4.5 (measured lowest hallucination). Input: question +
  full trajectory + `termination_reason`. Output:
  `{answer, evidence: [{step_index, tool, finding, time_range}], confidence:
  "high"|"medium"|"low"}`. Confidence is verbalized-only, no external calibration —
  flagged as the weakest/least-validated part of the system, explicitly tied to a
  separate resume project (Gamified Confidence Elicitation, ECE/Brier Score) as the
  natural future validation method.
- **Skill library** starts empty; bootstraps via: log everything → get gold labels
  (mechanism still open) → offline-diagnose failures → reward-gate candidate skills on
  a held-out split → matcher goes live only once skills exist.

## Canonical version system (from `Consolidated_Design_Decisions.docx`)

- Format Factory + Strategy pattern detects source format via file signature/magic
  bytes plus a text-density threshold (handles native PDF, HTML/EPUB, and scanned/OCR
  text uniformly), converging on one Canonical Text Schema.
- OCR strategy attaches a confidence score so low-confidence passages can be flagged
  for expert review or excluded from automated clustering.
- Retrieval uses MedCPT (query encoder for an AI's claim, article encoder for
  canonical entries) — chosen because it's a two-tower asymmetric model purpose-built
  for short-informal-text vs. long-formal-document matching, distinct from clustering
  (which uses PubMedBERT, a symmetric description-vs-description comparison).
  Retrieval narrows candidates; a separate grounding-check step (self-hosted semantic
  entailment model, still open) judges whether a claim actually holds up.
- AI-Raised-Questions loop: ungrounded/contradicted claims generate questions routed to
  Expert Review, not directly into Ratified Canonical — keeps canonical a living,
  continuously-refined artifact without letting unreviewed AI answers land in it
  directly. This is the pattern this repo's verdict/scorecard system loosely mirrors.

## Video encoder choice and alternatives considered

JHU-VPT(JEPA) (Shah, Xia, Vijay, Sikder, Vedula, Patel — MIDL 2025) is the primary/only
video embedder for the full system: self-supervised, pretrained on 2,591 unlabeled
cataract videos, predicts masked spatiotemporal tokens in latent space (reduces
sensitivity to low-level artifacts like reflections/blur). Output: ~196 tokens × 768
dims per 16-frame/250×250 clip (ViT-B/16-scale). Chosen over general-purpose surgical
foundation models (SurgMotion, built on V-JEPA 2 with a texture-sparse-scene
regularizer relevant to cataract's low-texture field, but ophthalmology is one of 13+
specialties in its training mix and it has no published cataract benchmark yet;
SurgRec-MAE/SurgRec-JEPA, more a benchmarking methodology than a drop-in model)
specifically because JHU-VPT(JEPA) is pretrained on Hopkins' own cataract corpus — the
tightest domain match available.

## Open items in the larger system (as of last SPARC session)

Deliberately deferred, tracked explicitly: mid-trajectory course-correction, m5's
Q-Former training-data gap, gold-label mechanism, malformed-orchestrator-action
handling, MedCPT domain-transfer validation, threshold tuning, `data_scope`
misclassification risk (no independent verification step), evaluation plan overall,
multi-reviewer workflow. Also unconfirmed: an internal annotation tool's JSON output
doesn't cleanly fit either pipeline as currently understood (parked pending
clarification); video-filename-to-caseId matching is assumed but unverified against
real files; copyright/TDM licensing status for paywalled and controlled-digital-lending
text sources is an open question.

## Why this repo exists relative to all of that

The full architecture above is a large, multi-quarter build with several genuinely open
research questions. This repo is a much smaller, faster first step: before building the
orchestrator/synthesizer/tool-suite/skill-matcher stack, get empirical signal on how far
off-the-shelf frontier multimodal models already are from canonical expert judgment on
real cataract footage, using nothing but sampled frames and a direct question. That
signal should inform decisions like the "generative vs. classification-style feedback"
open question in the larger system's Section 3.3, and the general "what does success
look like in year one" question planned for the Prof. Vedula call.
