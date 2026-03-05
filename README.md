# Talk2Code (v0.3.0 - The Neuro-Symbolic Update)

### Talk to your codebase. From anywhere.

You're walking your dog and suddenly realize how to fix that race condition. You're in bed at 2am and need to know if your auth module handles token refresh. You're on the bus wondering, "Wait, do we even have error handling for that edge case?"

*** *You're out with the kids and your production Vue app is suddenly throwing 404 errors to users. Fix it, immediately, without going into the office/server in-person. All you need is this bridge.* ***

**Just text your codebase and ask.**

Talk2Code is a neuro-symbolic bridge between your phone (via Telegram) and your local dev environment. It leverages the [opencode](https://github.com/nicepkg/opencode) CLI to give an autonomous coding assistant full access to your files, powered by a deterministic context engine that completely eliminates AI hallucinations.

---

## 🧠 The Engine: The Symbolic Reasoning Model (SRM)

*Research and Architecture by TinkerForge AI*

Traditional AI coding assistants rely on "Brute-Force RAG"—blindly string-matching keywords and dumping thousands of tokens of semi-relevant code into a massive context window. This leads to high latency, massive API costs, and architectural hallucinations.

Talk2Code solves the "Context Window Crisis" by introducing the **Symbolic Reasoning Model (SRM)**. It decouples *planning* from *syntax generation*.

### How the SRM Works:

1. **The Nerve (AST Graph Ingestion):** On boot, Talk2Code deterministically parses your Python/TypeScript codebase into a strict mathematical Directed Acyclic Graph (DAG) of discrete symbols (Classes, Functions, Imports).
2. **The Brain (MCTS Planner):** When you send a prompt, a local Monte Carlo Tree Search (MCTS) mathematically traverses the AST edges (`CALLS`, `IMPORTS`) to isolate the exact nodes required for your feature.
3. **The Bridge (Micro-Context):** The system rips out only the exact source code of the targeted nodes, generating a pristine, highly compressed XML payload (usually < 500 tokens).
4. **The Hands (OpenCode):** The cloud LLM receives a biologically sterile prompt devoid of conversational noise, allowing it to instantly write perfect, zero-shot code.

### 🧬 Synaptic Plasticity (Real-Time Learning)

A static graph is a dead graph. Talk2Code features **Synaptic Plasticity**. When the autonomous agent successfully writes new code to your disk, the SRM surgically hot-swaps the modified AST nodes in milliseconds. The Brain rewires its understanding of your architecture in real-time, zero-downtime required.

---

## ⚡ Core Application Features

* **Ambient AI Principles**: Optimized for speed, ingenuity, and keeping the human informed. Every long-running process is streamed live via Telegram chat bubbles.
* **Single-Shot Execution**: Toggle between conversational architectural brainstorming and silent, autonomous "Terminator" build mode.
* **Multi-Assistant Support**: Centralized manager to swap between coding assistants (Gemini, OpenCode, Codex). **OpenCode** (Minimax M2.5) is the current high-speed default.
* **Safety-First Restart**: Built-in syntax check before hot-reloading the daemon.

---

## 🛠️ Usage & Commands

The Talk2Code workflow is designed to be completely fluid from a mobile device:

* **Just type**: Brainstorm with the AI. It acts as a read-only architect, using the MCTS Brain to provide exact architectural plans without touching your files.
* **`#code [intent]`**: The **Target Lock**. Synthesizes your recent brainstorming into an actionable prompt, triggers the SRM to find the exact files, and unleashes the coding assistant to autonomously write the code.
* **`/clear`**: Wipes the conversation history. Use this to sterilize the context window before starting a completely new task to prevent cross-contamination.
* **`#<assistant> [prompt]`**: Route a specific prompt directly to a registered assistant (e.g., `#gemini fix the auth bug`).
* **`#model` / `#model #code**`: Switch between the curated plan/build models on the fly.
* **`#stop` / `/cancel**`: Manually terminate the active assistant run.

---

## 🚀 Quick Start

**1. Create a Telegram Bot**

* Message [@BotFather](https://t.me/BotFather) on Telegram
* Create a new bot, grab the **Token**

**2. Clone & Configure**

```bash
git clone https://github.com/dchisholm125/voice-to-code.git
cd voice-to-code
./setup.sh

```

Edit `.env` with your bot token and point `FILE_PATH` at your repository:

```bash
TELEGRAM_BOT_TOKEN=your_token_here
ALLOWED_USER_ID=your_telegram_id
FILE_PATH=/path/to/your/codebase

```

**3. Run**

```bash
./start_daemon.sh

```

*(Note: On first boot, you will see the SRM index your codebase into memory. This takes ~1 second per 1,000 symbols).*

---

## 📊 Observability & Recovery

* FastAPI + SSE expose `/observability/progress` for live frames and `/observability/sessions/{session_id}` for session state.
* The daemon outputs aggressive terminal logging for the SRM (`[SRM Brain]`, `[SRM Bridge]`), allowing you to mathematically verify exactly which AST nodes the system selected for every prompt.
* If the daemon crashes, `SessionManager` replays the ledger to warm-start the working set and keep the conversation alive.

---

## 📜 License

Apache 2.0 — Use it freely, keep the attribution.

---

*Built by Derek Chisholm / TinkerForge AI. Because your best coding ideas don't happen at your desk.*

---