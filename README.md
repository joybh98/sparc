# SPARC Evaluation

A small web app for probing how a frontier multimodal model reads a cataract surgery clip
compared with an expert reviewer. The model works as a tool-using agent: it can look at the
clip again before it answers, and every step it takes is recorded so the reviewer can
inspect and mark it.

- [Quick start](#quick-start)
- [What it does](#what-it-does)
- [UI structure](#ui-structure)
- [How a run works](#how-a-run-works)
- [API reference](#api-reference)
- [Evidence, confidence, uncertainty](#evidence-confidence-uncertainty)
- [Trajectory and per-call marks](#trajectory-and-per-call-marks)
- [Agents, tools and modalities](#agents-tools-and-modalities)
- [Data on disk](#data-on-disk)
- [Project layout](#project-layout)
- [Tests](#tests)

## Quick start

Developed on Python 3.12.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: add API keys (see below)
uvicorn app:app --reload --port 8000
```

Open <http://localhost:8000>.

**You can run it with no API keys.** Any model without a key falls back to a scripted mock
that runs the same agent loop (clearly labeled `(mock — no <KEY> set)`), so the whole app
works offline.

To use a real model, put its key in `.env`. The file is re-read on every request, so there
is no need to restart:

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIzaSy...        # Gemini Developer API key (aistudio.google.com/apikey)
DASHSCOPE_API_KEY=sk-...        # Qwen, via Alibaba Cloud Model Studio
```

Model names are set separately so a version change needs no code edit:
`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `GOOGLE_MODEL`, `QWEN_MODEL`. Qwen goes through
DashScope's OpenAI-compatible endpoint. `QWEN_BASE_URL` defaults to the international host;
use `https://dashscope.aliyuncs.com/compatible-mode/v1` for a mainland-China account.

Pick the model from the dropdown in the page header. It applies to both the analysis and
the chat.

## What it does

A session is either **text chat** or **video**, decided by whether a clip has been
uploaded to it.

1. **Chat** (no clip yet): talk to the model. It can search earlier messages, do
   arithmetic, or ask you a clarifying question.
2. **Upload a clip** (`.mp4`, `.mov`, `.webm`, `.mkv`, up to 500 MB). The moment it is
   read, an **analysis agent starts automatically** and its trace streams into the page.
   It ends with a step timeline (each step has a confidence, an uncertainty note and
   evidence frames) and an overall assessment.
3. **Review it**, with three tools:
   - **Mark your own steps:** scrub the player, fill start/end (there are "from
     playhead" helpers), and save them.
   - **Compare:** a table aligns each model step to your closest mark by temporal IoU
     and labels it match, partial, model-only or you-only.
   - **Mark each agent call:** every tool call in a trace gets correct / incorrect /
     unsure buttons.
4. **Talk to the model** about its analysis. A follow-up agent can re-read the earlier
   analysis, your marks and the chat so far, and look at the clip again on demand. It may
   ask you a clarifying question instead of guessing. Its answers are **grounded**:
   - each answer shows a confidence, what is uncertain, and the **evidence frames** it
     rests on (thumbnails that seek the player);
   - **timestamps in the text are links**: `1:23`, `12.5s` and `12.5–15s` jump the player;
   - the chat **uses the player's position as context**: scrub to a moment and ask "what's
     happening here?"; that moment's frame is sent along with the question.

Chat from before the upload carries over into the video session.

## UI structure

The UI is plain HTML and JavaScript with no build step, served from `static/`. Scripts
load in this order, and each owns one concern:

| File | Role |
| --- | --- |
| `index.html` | Page shell and CSS: a header with the model selector, and an empty `<main id="root">` that the panels fill. |
| `common.js` | Shared helpers (`$`, `esc`, `fmt`, `postJSON`), `linkify()` (timestamps in text become seek links), `confHTML()` and `evidenceHTML()` (confidence and evidence thumbnails, used by both the step table and chat answers), the global `App` state (session id, modalities, call verdicts), the **trace renderer**, the per-call **mark buttons**, the **agent picker**, and `runAgent()`, the streaming run client. Knows nothing about a specific modality. |
| `chat_panel.js` | `ChatPanel`: the chat thread, question box, agent picker, suggestion chips, and the player-position bar (`setPlayer()`). The same panel is reused in both text and video sessions. |
| `text_panel.js` | `TextPanel`: the screen before upload: an upload box above the chat. |
| `video_panel.js` | `VideoPanel`: the screen after upload (layout below). |
| `main.js` | Boot: loads the models and `/api/agents`, mounts `TextPanel`, and handles the upload that switches to `VideoPanel`. |

**Before upload** (`TextPanel`):

```
┌──────────────────────────────────────────────┐
│ header: title                  [Model ▾]     │
├──────────────────────────────────────────────┤
│ Upload a surgical clip   [file] [Upload]     │
│ Chat (agent picker, thread, question box)    │
└──────────────────────────────────────────────┘
```

**After upload** (`VideoPanel`):

```
┌───────────────────────┬──────────────────────────────────────┐
│ Clip                  │ Model's read                         │
│  video player         │  live trace  (tool calls, frames,    │
│                       │   correct/incorrect/unsure buttons)  │
│ Mark your own steps   │  step table: step · start · end ·    │
│  label, start/end,    │   confidence · comment/uncertainty · │
│  note, [Add mark]     │   evidence thumbnails                │
│  marks list           │  assessment                          │
│                       │  ▸ Re-run with a different agent     │
│                       ├──────────────────────────────────────┤
│                       │ Model steps vs. your steps (IoU)     │
│                       ├──────────────────────────────────────┤
│                       │ Talk to the model (ChatPanel)        │
│                       │  📍 Use player position 1:23 [frame] │
│                       │  answers: confidence · evidence ·    │
│                       │   clickable timestamps               │
└───────────────────────┴──────────────────────────────────────┘
```

Interactions worth knowing:

- Any frame thumbnail or evidence entry is clickable and **seeks the player**.
- The trace is collapsible. It shows each model call, each tool call with its arguments,
  result and latency, and thumbnails of the frames the agent fetched.
- **Mark buttons appear on a trace once the turn is saved**, not while it is still
  running. Clicking the active verdict clears it.
- **Re-run analysis** takes an optional hint ("what you expect to see") and an agent
  picker. Editing the agent's JSON there runs the edited config inline.
- Chat is blocked while an analysis is running.
- **Player position:** in a video session the chat shows a bar, "📍 Use the player position
  1:23 as context", with a thumbnail of that frame (updated when you pause or scrub). While
  it is ticked, each question is sent with the player's current time. Untick it to ask
  without that context.

## How a run works

```
Browser                         Server                              Model
  │ POST /api/query (stream)      │                                   │
  │──────────────────────────────▶│ validate agent, start thread      │
  │◀── {run_id} ──────────────────│                                   │
  │                               │  loop (up to max_iterations):     │
  │ GET /api/runs/<id>?after=N    │   call model ────────────────────▶│
  │──────────────────────────────▶│   ◀──────────── text + tool calls │
  │◀── new trace events ──────────│   run each tool, feed results back│
  │        (repeats every ~350ms) │   stop on the stop tool / no tools│
  │                               │  save turn → session JSON         │
  │◀── status: done + turn ───────│  write calls → trajectory.db      │
```

1. The UI sends `POST /api/query` with `mode: "agent"` and `stream: true`.
2. The server resolves the modality and agent, returns a `run_id` right away, and runs the
   loop in a background thread.
3. Each loop iteration is one model call. If the model asks for tools, the server runs
   them, appends the results, and calls the model again. For the analysis agent the loop
   ends when the model calls `submit_steps` successfully; if it stops without doing so,
   it is nudged once.
4. Every event (`model_call`, `tool_call`, `tool_result`, `nudge`, `error`, `final`) is
   appended to the run's trace. The UI polls `GET /api/runs/<id>?after=<seq>` and draws
   new events as they arrive.
5. When the run finishes, the **turn** (output, full trace, timing, agent config) is
   appended to the session JSON, and the trace is flattened into the SQLite trajectory
   store.

Images are never stored in the trace, only a small reference (`time_sec`, `zoom`, ...), and
the UI re-renders each frame on demand through `GET /api/sessions/<id>/frame`.

## API reference

All endpoints are JSON unless noted. Errors are returned as `{"error": "..."}` with HTTP
200, except where a status is noted.

### Models and agents

#### `GET /api/models`
Lists the selectable models: `{models: [{key, provider, model_name, has_key}]}`. `key` is
one of `gpt`, `claude`, `gemini`, `qwen`. `has_key: false` means that model will run on the
mock.

#### `GET /api/agents?session_id=<id>`
Everything the UI needs to drive agents. For each modality: its agents (re-read from disk on
every call), its tools (with JSON schemas), and any agent files that failed validation
(`invalid`). `active` names the modality the given session routes to (`video` if it has a
clip, otherwise `text`).

### Sessions and video

#### `POST /api/upload`
Multipart form: `file` (required) and `session_id` (optional; a new id is created if
omitted). Saves the clip to `sessions/<id>/video.mp4`. Returns
`{session_id, video_url, duration_sec}`. Rejects other extensions and files over 500 MB.

#### `GET /api/sessions/{session_id}/video`
Streams the uploaded clip.

#### `GET /api/sessions/{session_id}/frame?t=<sec>&zoom=1&cx=0.5&cy=0.5&w=480`
Returns one JPEG frame. `t` is the time in seconds. `zoom` (1–4) magnifies around the point
`(cx, cy)`, each 0–1. `w` is the max side in pixels (64–1280). Returns HTTP 404 if the frame
can't be read. This is how thumbnails in traces and evidence are drawn.

#### `GET /api/sessions/{session_id}`
The full session: `{session_id, created_at, video, turns, marks}`.

### Running the model

#### `POST /api/query`
The single endpoint that calls a model.

| Field | Meaning |
| --- | --- |
| `session_id` | Required. |
| `mode` | `"agent"` is the main mode, described below. `"steps"` and `"chat"` are older single-call modes that do not use tools, do not stream, and are not recorded in the trajectory store. |
| `agent` | Name of an agent file (for video: `default` or `followup`; for text: `chat`). Defaults to `default`, or the first agent. |
| `agent_config` | A full agent definition inline instead of `agent`. Validated the same way and saved on the turn. |
| `question` | The reviewer's question. Required for any agent whose `kind` is not `steps`. |
| `step_hint` | Steps agents only: what the reviewer expects to see. |
| `models` | `["gpt"]`: the first entry is the model. Falls back to the agent's own `model`, then `gpt`. |
| `n_frames` | Frames sampled up front (default 8, capped at 32). |
| `player_time_sec` | Optional. Where the reviewer's player is, in seconds. For video Q&A agents the server clamps it to the clip, adds "the player is at X s" to the message and attaches that frame. Ignored by steps agents and text sessions. Saved on the turn. |
| `stream` | `true` returns `{session_id, run_id}` immediately; poll `/api/runs/<id>`. `false` blocks and returns `{session_id, turn}`. |

A **turn** looks like (abridged):

```json
{
  "turn_index": 0, "type": "steps", "agent": "default", "model": "...", "provider": "...",
  "latency_ms": 1234, "iterations": 4, "stop_reason": "tool:submit_steps",
  "model_steps": [{"step_label": "...", "start_sec": 0, "end_sec": 4.5, "comment": "...",
                   "confidence": 0.7, "uncertainty": "...",
                   "evidence": [{"time_sec": 1.2, "note": "..."}]}],
  "assessment": "...",
  "trace": [{"seq": 1, "type": "model_call", "...": "..."}],
  "error": null
}
```

`type` is `steps` for a steps agent (fills `model_steps` and `assessment`) or
`followup` / `chat` for Q&A (fills `question`, `answer`, `needs_reply`, and `parent_turn`,
the index of the analysis it follows). `needs_reply: true` means the model asked you a
clarifying question. A video follow-up that finished with `submit_answer` also carries
`confidence`, `uncertainty` and `evidence` (same shape as for steps), and
`player_time_sec` if one was sent.

#### `GET /api/runs/{run_id}?after=<seq>`
Polls a streaming run. Returns `{status, error, turn, events}` where `events` are the trace
events with `seq` greater than `after`. `status` is `running`, `done` or `error`; `turn` is
set when done. Returns HTTP 404 for an unknown or expired run (finished runs are kept for an hour).

### Reviewer input

#### `POST /api/mark`
Records one of your own steps: `{session_id, step_label, start_sec, end_sec, note?}`.
Returns `{ok, marks}` with all marks for the session. Follow-up agents can read these through
the `get_reviewer_marks` tool.

#### `POST /api/calls/mark`
Records your verdict on one recorded call: `{call_id, verdict, note?}`. `verdict` is
`correct`, `incorrect`, `unsure`, or `null` to clear it. It does not call a model. Returns
`{ok: true}`, or an error for an unknown `call_id` or invalid verdict.

#### `GET /api/sessions/{session_id}/trajectory`
Every recorded call for the session, in order: `{session_id, calls: [...]}`. See
[Trajectory and per-call marks](#trajectory-and-per-call-marks) for the row shape.

#### `GET /api/sessions/{session_id}/evidence`
Every cited frame for the session: `{session_id, evidence: [...]}`, one row per frame with
its source (`step` or `answer`), confidence, uncertainty and the `call_id` of the call that
delivered it.

## Evidence, confidence, uncertainty

Each step the analysis agent submits through `submit_steps` carries:

| Field | Required | Meaning |
| --- | --- | --- |
| `confidence` | yes | Number from 0 to 1: the model's probability that the step's label and time range are right. |
| `evidence` | yes (may be `[]`) | `[{time_sec, note}]`: frames that support the step and what is visible in them. |
| `uncertainty` | no | What is unclear, or what would resolve it. |

`submit_steps` validates these (and times within the clip, chronological order). If anything
is invalid, it returns an error to the agent, which has to fix it and call again. In the UI,
confidence is colored (≥75% green, ≥45% amber, otherwise red), uncertainty is shown under
the comment, and each evidence entry is a thumbnail that seeks the player.

Follow-up answers have the same three fields: the video `followup` agent delivers its answer
through `submit_answer` (`answer`, `confidence`, `evidence`, optional `uncertainty`), which
is validated the same way. The answer card shows them under the text. If a model replies
without calling the tool it is nudged once, then its plain text is used (no confidence or
evidence).

## Trajectory and per-call marks

The session JSON keeps each turn's full trace. In addition, every saved turn is flattened
into a SQLite database, `sessions/trajectory.db`, table `calls`, so the question "which
call invoked what" is easy to query and your verdicts are stored beside the calls.

Rows form a tree: **run → model call → tool call**.

| Column | Meaning |
| --- | --- |
| `call_id` | `<session>:<turn>:<trace seq>`; the root row is `<session>:<turn>:run`. |
| `parent_id`, `caller` | What invoked this call: a tool's parent is the model call of the same iteration; a model call's parent is the run. |
| `kind` | `run`, `model_call`, `tool` or `error`. |
| `name` | The agent, the model, or the tool name. |
| `iteration`, `seq`, `started_at`, `latency_ms` | Position and timing. |
| `args_json`, `result_text`, `result_json`, `is_error` | What was sent and what came back. |
| `reviewer_verdict`, `reviewer_note`, `marked_at` | Your mark, if any. |

### Evidence table

`evidence` holds one row per cited frame, so confidence and evidence are plain columns
rather than JSON inside a call. A step or answer that cites nothing still gets one row with
`time_sec` NULL, so its confidence is kept. Rows are written when a turn is recorded, and
only for a successful `submit_steps` / `submit_answer`.

| Column | Meaning |
| --- | --- |
| `call_id` | The `submit_steps` / `submit_answer` call that delivered it; joins to `calls`, so a verdict you gave that call applies to its evidence. |
| `session_id`, `turn_index` | Where it came from. |
| `source` | `step` or `answer`. |
| `step_index`, `step_label` | Which step (NULL for an answer). |
| `confidence`, `uncertainty` | The model's own, for that step or answer. |
| `time_sec`, `note` | The cited frame and what is visible in it. |

The run row in `calls` also records the `question` and `player_time_sec` it was asked with.

Example queries:

```bash
# everything as CSV
sqlite3 -header -csv sessions/trajectory.db "select * from calls" > calls.csv

# every frame cited for calls you marked incorrect
sqlite3 -header sessions/trajectory.db \
  "select e.turn_index, e.source, e.step_label, e.confidence, e.time_sec, e.note
   from evidence e join calls c using (call_id) where c.reviewer_verdict = 'incorrect'"

# which tools the model called, and how you judged them
sqlite3 sessions/trajectory.db \
  "select name, reviewer_verdict, count(*) from calls where kind='tool' group by 1, 2"
```

## Agents, tools and modalities

Everything is organized by **modality**: a session with an uploaded clip uses `video`,
otherwise `text`.

- **Agents are JSON files** at `modalities/<modality>/agents/*.json`. Fields: `name`,
  `description`, `system_prompt`, `tools` (a whitelist), `kind` (`steps` or `followup`),
  `stop_when` (`no_tool_calls` or `tool:<name>`), `max_iterations`, `initial_frames`,
  `required_output`, `mock`, `model`. Files are re-read on every request, so a new or
  edited agent needs no restart. Typos, unknown tools and out-of-range values are reported
  by `GET /api/agents` under `invalid`.
- **Built in:** video has `default` (step analysis) and `followup` (Q&A); text has `chat`.
- **Video tools:** `get_clip_info`, `sample_frames`, `zoom_frame`, `submit_steps`,
  `submit_answer`, `get_prior_analysis`, `get_reviewer_marks`, `ask_user`. **Text tools:** `search_history`,
  `calculate`, `ask_user`.
- **Add a tool:** one decorated function in `modalities/<modality>/tools.py`:
  `@tool("video", "name", "description", {json schema})`, returning a `ToolResult`. Configs
  can only name registered tools, so they can never run arbitrary code.
- **Add a modality:** a package under `modalities/` exposing `MODALITY` (see
  `modalities/video/__init__.py`). It is auto-discovered, and a broken one is skipped and
  reported rather than breaking the app.
- **In the UI:** the chat and the "re-run analysis" box each have an agent picker with an
  editable config. Edit the JSON and the edited version runs as an inline `agent_config`.

## Data on disk

All under `sessions/` (git-ignored):

- `sessions/<id>.json`: the session: `video`, `turns` (each with its full trace), `marks`.
- `sessions/<id>/video.mp4`: the uploaded clip.
- `sessions/trajectory.db`: the queryable call trajectory (`calls`), the cited frames
  (`evidence`) and your per-call verdicts.

Sessions have no auth and no retention policy, so this is meant for local, single-user use.
The session files double as a rough seed of gold labels.

## Project layout

```
app.py                  FastAPI app: all endpoints, run orchestration, session storage
frames.py               frame sampling and zoom (OpenCV)
providers.py            older single-call model path (modes "chat"/"steps")
core/
  loop.py               the agent loop and trace events
  llm.py                provider adapters (OpenAI, Anthropic, Google, Qwen) + mock plumbing
  registry.py           modality discovery, agent config loading and validation
  tools.py              @tool decorator, ToolContext, ToolResult
  trajectory.py         SQLite trajectory store and per-call marks
modalities/
  video/                __init__.py, tools.py, mock.py, agents/{default,followup}.json
  text/                 __init__.py, tools.py, mock.py, agents/chat.json
static/                 the UI (see "UI structure")
tests/                  offline tests
docs/                   DECISIONS.md (why things are the way they are), NEXT_STEPS.md
```

This is deliberately a small slice of the larger SPARC design (no source adapter, no
MedCPT skill matcher, no orchestrator/synthesizer split, no self-hosted routing). It answers
a narrow question first: how does an off-the-shelf frontier model's step segmentation of a
clip compare to a reviewer's? See `docs/DECISIONS.md` for the history and `docs/BROADER_ARCHITECTURE.md` for the larger design.

## Tests

All offline, using the mock provider and a generated clip:

```bash
python -m unittest discover -s tests -t .
```
