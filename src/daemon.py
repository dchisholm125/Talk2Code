import os
import asyncio
import subprocess
import logging
import re
from openai import OpenAI
from telegram import Update, ForceReply
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text)

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
FILE_PATH = os.getenv("FILE_PATH")
HF_TOKEN = os.getenv("HF_TOKEN")

# Default model - Qwen3 30B MoE (only 3B active params = fast + smart)
# Change this to any model available on HuggingFace Inference Providers
HF_MODEL = os.getenv("HF_MODEL", "Qwen/Qwen3-30B-A3B")

# Initialize HuggingFace client (if token provided)
hf_client = None
if HF_TOKEN:
    hf_client = OpenAI(
        base_url="https://router.huggingface.co/v1",
        api_key=HF_TOKEN,
    )
    logger.info(f"HuggingFace client initialized with model: {HF_MODEL}")
else:
    logger.warning("No HF_TOKEN found - /think commands will be unavailable")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    logger.info(f"User {user.id} ({user.username}) started the bot.")

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        await update.message.reply_html(f"Unauthorized access. Your ID: {user.id}")
        return

    think_status = "enabled" if hf_client else "disabled (no HF_TOKEN)"
    await update.message.reply_html(
        f"Hi {user.mention_html()}! Talk2Code is ready.\n\n"
        f"<b>Commands:</b>\n"
        f"Just type → runs against your codebase via opencode\n"
        f"/think [question] → AI architect (Qwen3 via HuggingFace) [{think_status}]\n"
        f"/code [question] → explicit codebase query via opencode\n"
        f"/model [name] → switch HuggingFace model",
        reply_markup=ForceReply(selective=True),
    )


async def run_opencode(prompt: str) -> str:
    """Runs the opencode command and returns the output."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["opencode", "run", prompt],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=FILE_PATH
        )

        output = result.stdout
        if result.stderr:
            output += f"\n\nSTDERR:\n{result.stderr}"

        output = strip_ansi(output)

        if not output.strip():
            return "Command executed but returned no output."

        return output
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 120 seconds."
    except FileNotFoundError:
        return "Error: 'opencode' command not found. Is it installed and in PATH?"
    except Exception as e:
        return f"Error executing command: {str(e)}"


async def run_think(prompt: str) -> str:
    """Runs a prompt against HuggingFace Inference Providers (Qwen3)."""
    if not hf_client:
        return "Error: HuggingFace not configured. Add HF_TOKEN to your .env file."

    try:
        completion = await asyncio.to_thread(
            hf_client.chat.completions.create,
            model=HF_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior software architect. Give concise, actionable "
                        "advice. Focus on architecture decisions, trade-offs, and "
                        "practical implementation guidance. Keep responses under 500 words "
                        "unless the question demands more depth."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=1024,
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Error from HuggingFace: {str(e)}"


async def handle_think(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /think command - route to HuggingFace AI."""
    user = update.effective_user

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized access attempt from {user.id}")
        return

    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /think <your architecture question>")
        return

    logger.info(f"[think] Received: {prompt}")
    status_msg = await update.message.reply_text(f"Thinking via {HF_MODEL}...")

    output = await run_think(prompt)
    await send_chunked(update, context, status_msg, output)


async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /code command - explicit route to opencode."""
    user = update.effective_user

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized access attempt from {user.id}")
        return

    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /code <your codebase question>")
        return

    logger.info(f"[code] Received: {prompt}")
    status_msg = await update.message.reply_text("Running opencode...")

    output = await run_opencode(prompt)
    await send_chunked(update, context, status_msg, output)


async def handle_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /model command - switch the HuggingFace model."""
    global HF_MODEL
    user = update.effective_user

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        return

    new_model = " ".join(context.args) if context.args else ""
    if not new_model:
        await update.message.reply_text(
            f"Current model: {HF_MODEL}\n\n"
            f"Usage: /model <model_id>\n"
            f"Examples:\n"
            f"  /model Qwen/Qwen3-8B\n"
            f"  /model Qwen/Qwen3-Coder-30B-A3B-Instruct\n"
            f"  /model meta-llama/Llama-3.3-70B-Instruct"
        )
        return

    HF_MODEL = new_model
    logger.info(f"Model switched to: {HF_MODEL}")
    await update.message.reply_text(f"Model switched to: {HF_MODEL}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Default handler - route plain messages to opencode."""
    user = update.effective_user

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized access attempt from {user.id}")
        return

    prompt = update.message.text
    logger.info(f"[opencode] Received: {prompt}")

    status_msg = await update.message.reply_text("Running opencode...")

    output = await run_opencode(prompt)
    await send_chunked(update, context, status_msg, output)


async def send_chunked(update, context, status_msg, output):
    """Send output in chunks respecting Telegram's 4096 char limit."""
    MAX_LENGTH = 4000
    chunks = [output[i:i+MAX_LENGTH] for i in range(0, len(output), MAX_LENGTH)]

    if chunks:
        # Edit the status message with first chunk
        try:
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=status_msg.message_id,
                text=f"```\n{chunks[0]}\n```",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            # Fallback if markdown parsing fails
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=status_msg.message_id,
                text=chunks[0]
            )

        for chunk in chunks[1:]:
            try:
                await update.message.reply_text(
                    f"```\n{chunk}\n```",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                await update.message.reply_text(chunk)
    else:
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=status_msg.message_id,
            text="No output received."
        )


def main() -> None:
    """Start the bot."""
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in .env file")
        return

    application = Application.builder().token(TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("think", handle_think))
    application.add_handler(CommandHandler("code", handle_code))
    application.add_handler(CommandHandler("model", handle_model))

    # Default: plain text goes to opencode
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Talk2Code daemon started. Listening for messages...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()