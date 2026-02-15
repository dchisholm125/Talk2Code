# Voice-to-Code Bridge Daemon

This daemon acts as a bridge between your Android device (via Telegram) and your local development environment. It receives commands through a chat interface, executes them using `opencode`, and returns the output.

## Setup

1.  **Create a Telegram Bot**:
    *   Message `@BotFather` on Telegram.
    *   Create a new bot and get the **Token**.

2.  **Configure Environment**:
    *   Run `./setup.sh` to create the virtual environment and install dependencies.
    *   Edit `.env` and paste your `TELEGRAM_BOT_TOKEN`.
    *   (Optional) Add your Telegram User ID to `ALLOWED_USER_ID` to restrict access. You can find your ID by messaging `@userinfobot`.

3.  **Run the Daemon**:
    *   Run `./start_daemon.sh`.
    *   The bot will start polling for messages.

## Usage

*   Send any text message to the bot.
*   The bot will execute `opencode run "<your_message>"`.
*   The output (stdout/stderr) will be sent back to you.

## Requirements

*   Python 3.8+
*   `opencode` CLI tool installed and in your PATH.
