# Talk2Code (v0.3.0)

### Talk to your codebase. From anywhere.

You're walking your dog and suddenly realize how to fix that race condition. You're in bed at 2am and need to know if your auth module handles token refresh. You're on the bus wondering "wait, do we even have error handling for that edge case?" 

*** *You're out with the kids and your production Vue app is suddenly throwing 404 errors to users. Fix it, immediately, without going into the office/server in-person. All you need is this bridge.* ***

**Just text your codebase and ask.**

Talk2Code is a bridge between your phone (via Telegram) and your local dev environment. It leverages the [opencode](https://github.com/nicepkg/opencode) CLI to give a high-quality coding assistant full access to your files.

---

- **Ambient AI Principles**: Optimized for speed, ingenuity, and keeping the human informed. Every long-running process (like compression) is streamed live.
- **Multi-Assistant Support**: Centralized manager to swap between coding assistants (Gemini, OpenCode, Codex). **OpenCode** (Minimax M2.5) is the current high-speed default.
- **Low-Latency Architecture**: Designed for "ambient coding" — enabling rapid, small-scale remote fixes from a phone via Telegram.
- **Model Rotation**: Automatic fallback if rate limits or errors (429, 401, 402) are encountered.
- **Real-time Streaming**: See the bot's "thinking" process, compression steps, and tool usage live.
- **Conversational Bubbles**: Response text is streamed into distinct chat bubbles for chronological clarity.
- **Session Memory**: Accomplishments are summarized and carried forward.
- **Safety-First Restart**: Built-in syntax check before hot-reloading.

---

## 🛠️ Usage

### Commands
- **Just type**: Brainstorm with the AI. It acts as an architect/partner.
- **`#code [focus]`**: Synthesize the recent brainstorming into an actionable prompt for the **default** coding assistant.
- **`#<assistant> [prompt]`**: Route a specific prompt directly to a registered assistant (e.g., `#gemini fix the auth bug`).
- **`#solo [thoughts]`**: Log your thoughts silently.
- **`#model` / `#model #code`**: Switch between the curated plan/build models on the fly (reply with the number from the list).
- **`#stop`**: Manually terminate the active assistant or compression session.
- **`#restart`**: Validates and restarts the daemon.
- **`/clear`**: Wipes history.
- **`/cancel`**: Terminates the active assistant run.

---

## ⚡ Quick Start

**1. Create a Telegram Bot**
- Message [@BotFather](https://t.me/BotFather) on Telegram
- Create a new bot, grab the **Token**

**2. Clone & Configure**
```bash
git clone https://github.com/dchisholm125/voice-to-code.git
cd voice-to-code
./setup.sh
```
Edit `.env` with your bot token and point `FILE_PATH` at your repo:
```bash
TELEGRAM_BOT_TOKEN=your_token_here
ALLOWED_USER_ID=your_telegram_id
FILE_PATH=/path/to/your/codebase
```

**3. Run**
```bash
./start_daemon.sh
```

---

## ⚙️ Configuration
- `LOG_LEVEL`: Controls logger verbosity (`INFO` by default).
- `LOG_PATH`: Location of the rotating log file (`~/.voice-to-code/app.log`).
- `OBSERVABILITY_HOST` / `OBSERVABILITY_PORT`: Where the FastAPI observability server (progress stream + session detail endpoint) listens.
- `OPENCODE_PLAN_MODEL` / `OPENCODE_BUILD_MODEL`: Override the assistant used for plan/code agents in OpenCode.
- `FILE_PATH`: Point this at the repository the assistant is allowed to edit.

> Every session is managed by the backend and persisted under `~/.voice-to-code` (`sessions-state.json` + `event-ledger.jsonl`), so reconnects replay directly from the master narrative log.

## 🧠 Session-Centric Architecture
1. Each chat maps to a persistent `SessionID` and `SessionState` stored by `SessionManager` in `~/.voice-to-code/sessions-state.json`.
2. The frontend consumes `/observability/sessions/{session_id}` as the single source of truth for summaries, context envelopes, and working sets.
3. The `ContextEngine` produces a `ContextEnvelope` (intent, entities, discovery circles, git history, docs, tests, working set) emitted as `ContextSnapshotTaken` so downstream agents always get the full picture.
4. The event ledger at `~/.voice-to-code/event-ledger.jsonl` captures every heartbeat (`VoiceCaptured`, `IntentExtracted`, `LLM_Thought_Started`, `ToolExecution`, `StateUpdate`, etc.) alongside the "why", enabling warm-start rehydration.

## 🔍 Ambient Discovery
1. The LLM extracts intent + entities, and the `ContextEngine` expands the search in concentric circles: direct files, functional dependencies, validation suites, and contextual docs/history.
2. These artifacts build the `ContextEnvelope`, ensuring the assistant never loses sight of the relevant working set.
3. The frontend renders those envelopes so you always see the live context behind the assistant’s progress.

## 📊 Observability & Recovery
- FastAPI + SSE expose `/observability/progress` for live frames and `/observability/sessions/{session_id}` for session state plus the logged narrative.
- The Narrative Log records every decision with explicit reasoning (e.g., "Expanded to `test_auth.py` for validation coverage") so debugging is a single replay kit.
- If the daemon crashes, `SessionManager` replays the ledger to warm-start the working set and keep the conversation alive.

---

## 🏗️ How It Works

1. **User Message** → Telegram → `daemon.py`
2. **Context Assembly** → Current chat history + Session Summaries.
3. **Multi-Assistant Manager** → Routes prompts to OpenCode, Gemini, or other CLI tools.
4. **Execution Layer** → Subprocess execution with real-time JSON/text streaming.
5. **Intermediate Layer** → `telegram_formatter.py` ensures valid Telegram HTML.

---

## 📜 License

Apache 2.0 — Use it freely, keep the attribution.

---

*Built by Derek Chisholm. Because your best coding ideas don't happen at your desk.*
