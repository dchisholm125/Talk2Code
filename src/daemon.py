import os
import asyncio
import subprocess
import logging
import re
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    logger.info(f"User {user.id} ({user.username}) started the bot.")
    
    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        await update.message.reply_html(f"Unauthorized access. Your ID: {user.id}")
        return

    await update.message.reply_html(
        f"Hi {user.mention_html()}! Send me a prompt to run via opencode.",
        reply_markup=ForceReply(selective=True),
    )

async def run_opencode(prompt: str) -> str:
    """Runs the opencode command and returns the output."""
    try:
        # Use asyncio.to_thread to run the blocking subprocess call
        # This prevents blocking the event loop
        result = await asyncio.to_thread(
            subprocess.run,
            ["opencode", "run", prompt],
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    user = update.effective_user
    
    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized access attempt from {user.id}")
        return

    prompt = update.message.text
    logger.info(f"Received prompt: {prompt}")
    
    # Notify user we are running it
    status_msg = await update.message.reply_text("Running opencode...")
    
    # Run the command
    output = await run_opencode(prompt)
    
    # Telegram message limit is 4096 characters. Split if needed.
    # We'll use a simple chunking strategy.
    MAX_LENGTH = 4000
    
    chunks = [output[i:i+MAX_LENGTH] for i in range(0, len(output), MAX_LENGTH)]
    
    # Delete the "Running..." message or edit it with the first chunk
    if chunks:
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=status_msg.message_id,
            text=f"```\n{chunks[0]}\n```",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Send remaining chunks
        for chunk in chunks[1:]:
            await update.message.reply_text(
                f"```\n{chunk}\n```",
                parse_mode=ParseMode.MARKDOWN
            )
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

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
