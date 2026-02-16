# Talk2Code

### Talk to your codebase. From anywhere.

You're walking your dog and suddenly realize how to fix that race condition. You're in bed at 2am and need to know if your auth module handles token refresh. You're on the bus wondering "wait, do we even have error handling for that edge case?" 

**You're out with the kids and your production Vue app is suddenly throwing 404 errors to users. Fix it, immediately, without going into the office/server in-person. All you need is this bridge.**

**Just text your codebase and ask.**

Talk2Code is a dead-simple bridge between your phone (via Telegram) and your local dev environment. You send a message, it runs against your codebase through [opencode](https://github.com/nicepkg/opencode), and you get the answer back. That's it. ~150 lines of Python. No cloud. No SaaS. No BS.

> "I wonder if we handle that..." → text your codebase → get the answer → keep walking.

---

## What It Looks Like

```
You:     "Do we have retry logic in our API client?"
Talk2Code: "Yes — src/api/client.ts implements exponential 
           backoff with 3 retries in the `fetchWithRetry` 
           function (line 47)..."

You:     "What would it take to add WebSocket support?"
Talk2Code: "Based on the current architecture, you'd need to..."
```

Your coding assistant, in your pocket, with full context of your actual codebase. Voice-to-text works natively — just talk.

---

## Quick Start

**1. Create a Telegram Bot**
- Message [@BotFather](https://t.me/BotFather) on Telegram
- Create a new bot, grab the **Token**

**2. Clone & Configure**
```bash
git clone https://github.com/YOUR_USERNAME/talk2code.git
cd talk2code
./setup.sh
```
Edit `.env` with your bot token and point `FILE_PATH` at whatever repo you want to talk to:
```
TELEGRAM_BOT_TOKEN=your_token_here
ALLOWED_USER_ID=your_telegram_id
FILE_PATH=/path/to/your/codebase
```
> Find your Telegram ID by messaging [@userinfobot](https://t.me/userinfobot)

**3. Run**
```bash
./start_daemon.sh
```

That's it. Open Telegram, text your bot, talk to your code.

---

## Requirements

- Python 3.8+
- [opencode](https://github.com/nicepkg/opencode) CLI installed and in PATH
- A Telegram account

## How It Works

It's intentionally simple. A Telegram bot polls for your messages, pipes them to `opencode run` pointed at your chosen repo, and sends back the response. No server, no cloud, no API keys beyond Telegram. Your code never leaves your machine.

## Why This Exists

Every developer has thoughts about their code when they're away from their keyboard. Until now, those thoughts either got forgotten or scribbled in a notes app to deal with "later." Talk2Code closes that gap — your codebase is always one text message away.

---

## Roadmap

This is v0.1.0 — raw, early, and already indispensable. Coming next:

- [ ] Multi-repo switching (currently monopath)
- [ ] Auto-detect modes for higher quality interactions (ambient refactor? just planning? need research?)
- [ ] Native chat interface plus plugins (Slack, Discord, etc.)
- [ ] Conversation memory (follow-up questions with context)
- [ ] Session logging (capture your ambient ideas for later review)

---

## License

Apache 2.0 — Use it freely, keep the attribution.

---

*Built by Derek Chisholm. Because your best coding ideas don't happen at your desk.*