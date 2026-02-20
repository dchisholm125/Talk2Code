# Talk2Code (v0.3.0)

### Talk to your codebase. From anywhere.

You're walking your dog and suddenly realize how to fix that race condition. You're in bed at 2am and need to know if your auth module handles token refresh. You're on the bus wondering "wait, do we even have error handling for that edge case?" 

*** *You're out with the kids and your production Vue app is suddenly throwing 404 errors to users. Fix it, immediately, without going into the office/server in-person. All you need is this bridge.* ***

**Just text your codebase and ask.**

Talk2Code is a bridge between your phone (via Telegram) and your local dev environment. It leverages the [opencode](https://github.com/nicepkg/opencode) CLI to give a high-quality coding assistant full access to your files.

---

- **Multi-Assistant Support**: Centralized manager to swap between coding assistants (Gemini, OpenCode, etc.). **Gemini 3 Flash** is now the default.
- **Model Rotation**: Automatic fallback from Flash to Pro if rate limits or errors (429, 401, 402) are encountered.
- **Assistant Hashtags**: Direct your prompts to specific assistants using hashtags (e.g., `#gemini <prompt>` or `#opencode <prompt>`).
- **Real-time Streaming**: See the bot's "thinking" process and tool usage live.
- **Conversational Bubbles**: Response text is streamed into chat bubbles as it's generated.
- **Session Memory**: Accomplishments are summarized and carried forward.
- **Safety-First Restart**: Built-in syntax check before hot-reloading.

---

## 🛠️ Usage

### Commands
- **Just type**: Brainstorm with the AI. It acts as an architect/partner.
- **`#code [focus]`**: Synthesize the recent brainstorming into an actionable prompt for the **default** coding assistant.
- **`#<assistant> [prompt]`**: Route a specific prompt directly to a registered assistant (e.g., `#gemini fix the auth bug`).
- **`#solo [thoughts]`**: Log your thoughts silently.
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