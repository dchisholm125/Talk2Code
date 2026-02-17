import os
import asyncio
import subprocess
import logging
import re
import pty
import threading
import select
from telegram import Update, ForceReply
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load environment variables BEFORE other imports
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'), override=True)

from context_manager import ContextManager
from prompt_builder import PromptBuilder
from groq_client import groq_chat, is_configured as groq_configured

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
THINK_TAGS = re.compile(r'<think>.*?</think>', re.DOTALL)
STDERR_NOISE = re.compile(r'\n*STDERR:\n.*', re.DOTALL)

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text)

def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from model responses."""
    return THINK_TAGS.sub('', text).strip()

def clean_opencode_output(text: str) -> str:
    """Clean up opencode output: remove ANSI codes, STDERR noise, trailing artifacts."""
    text = strip_ansi(text)
    # Remove STDERR block and everything after it
    text = STDERR_NOISE.sub('', text)
    # Remove common trailing build artifacts
    text = re.sub(r'\n*> build[\s\S]*$', '', text)
    # Collapse excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def is_code_block(text: str) -> bool:
    """Detect if text contains code-like content."""
    text_lower = text.lower()
    code_indicators = [
        'def ', 'class ', 'function ', 'const ', 'let ', 'var ',
        'import ', 'from ', 'export ', 'return ', 'if (', 'if(', 'for (', 'for(',
        'while ', 'switch ', 'match ', '=>', '->', 'async ', 'await ',
        '#!/', 'package ', 'interface ', 'type ', 'enum ',
        '```', '```python', '```js', '```ts', '```bash', '```sh',
    ]
    file_patterns = [
        r'\.(py|js|ts|jsx|tsx|json|yml|yaml|toml|md|html|css|sh|bash)$',
        r'src/|lib/|app/|components/|utils/|helpers/',
        r'/[a-zA-Z0-9_]+\.(py|js|ts|jsx|tsx)$',
    ]
    for indicator in code_indicators:
        if indicator in text_lower:
            return True
    for pattern in file_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def is_message_boundary(text: str) -> bool:
    """Detect logical breaks in OpenCode output where we should send a new Telegram message."""
    text_clean = strip_ansi(text)
    if len(text_clean) < 10:
        return False
    patterns = [
        r'^[📁📂🔧✅❌⚠️🔨🚀💻]',  # Emoji prefixes
        r'^>',  # OpenCode prompt/response marker
        r'^Reading|^Analyzing|^Creating|^Modifying|^Executing|^Running|^Building',
        r'^(src/|lib/|app/|components/|utils/|tests/|test/)',
        r'\.py:|\.js:|\.ts:|\.tsx:|\.json:|\.yml:',  # File:line references
        r'modified:|created:|deleted:|updated:',  # File change indicators
        r'^\s*```',  # Code block start/end
        r'\?\s*$',  # Question ending
        r'\(y/n\)|\(yes/no\)|continue\?|proceed\?',  # Interactive prompts
    ]
    for pattern in patterns:
        if re.search(pattern, text_clean, re.MULTILINE | re.IGNORECASE):
            return True
    return '\n\n' in text_clean or len(text_clean) > 500


def format_for_telegram(text: str, is_code: bool = None) -> str:
    """Format output for Telegram with appropriate markdown."""
    text = strip_ansi(text).strip()
    if not text:
        return ""
    if is_code is None:
        is_code = is_code_block(text)
    if is_code:
        return f"```\n{text}\n```"
    return text


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
FILE_PATH = os.getenv("FILE_PATH")

CODING_ASSISTANT_CLI_COMMAND = os.getenv(
    "CODING_ASSISTANT_CLI_COMMAND",
    "opencode run -m opencode/big-pickle {prompt}"
)

OPENCODE_TIMEOUT = int(os.getenv("OPENCODE_TIMEOUT", "300"))

SIGNATURE_OPENCODE = "🤖 Powered by OpenCode"
SIGNATURE_THINK = "🧠 Powered by Llama (Groq)"

CONTEXT_HISTORY_PATH = os.getenv("CONTEXT_HISTORY_PATH", "context_history.json")
context_manager = ContextManager(history_path=CONTEXT_HISTORY_PATH, max_entries=10)
prompt_builder = PromptBuilder(context_manager)

if groq_configured():
    from groq_client import GROQ_MODEL
    logger.info(f"Groq client configured with model: {GROQ_MODEL}")
else:
    logger.warning("No GROQ_API_KEY found - /think commands will be unavailable")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    logger.info(f"User {user.id} ({user.username}) started the bot.")

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        await update.message.reply_html(f"Unauthorized access. Your ID: {user.id}")
        return

    think_status = "enabled" if groq_configured() else "disabled (no GROQ_API_KEY)"
    context_count = context_manager.get_entry_count()
    await update.message.reply_html(
        f"Hi {user.mention_html()}! Talk2Code is ready.\n\n"
        f"<b>Commands:</b>\n"
        f"Just type → runs against your codebase via opencode\n"
        f"/think [question] → AI architect [{think_status}]\n"
        f"/code [question] → explicit codebase query via opencode\n"
        f"/clearcontext → clear conversation memory ({context_count} entries)",
        reply_markup=ForceReply(selective=True),
    )


async def run_opencode(prompt: str) -> str:
    """Runs the opencode command and returns the output."""
    try:
        # DEBUG: Log environment info
        logger.info(f"FILE_PATH: {FILE_PATH}")
        logger.info(f"Prompt length: {len(prompt)}")

        # Check if opencode is in PATH
        check_proc = await asyncio.create_subprocess_exec(
            "which", "opencode",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        which_stdout, _ = await check_proc.communicate()
        logger.info(f"opencode path: {which_stdout.decode().strip()}")

        # Build command from template, substituting {prompt}
        command = CODING_ASSISTANT_CLI_COMMAND.format(prompt=prompt)
        cmd_parts = command.split()

        # stdin=DEVNULL: prevent CLI from blocking on permission prompts
        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=FILE_PATH
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=OPENCODE_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return (
                f"Timed out after {OPENCODE_TIMEOUT}s. "
                "opencode may have been waiting for input or permission. "
                "Try rephrasing your prompt to be more specific."
            )

        # DEBUG: Log output details
        stdout_text = stdout.decode('utf-8', errors='replace').strip()
        stderr_text = stderr.decode('utf-8', errors='replace').strip()
        
        # Clean stderr to check if it has meaningful content
        stderr_clean = strip_ansi(stderr_text)
        
        logger.info(f"stdout length: {len(stdout_text)}, stderr length: {len(stderr_clean)}, returncode: {process.returncode}")
        logger.info(f"stdout preview: {stdout_text[:200]!r}")
        logger.info(f"stderr preview: {stderr_clean[:200]!r}")

        if stdout_text:
            output = stdout_text
            if stderr_clean:
                output += f"\n\n⚠️ Warnings:\n{stderr_clean}"
        elif stderr_clean:
            output = f"⚠️ Error:\n{stderr_clean}"
        else:
            output = f"⚠️ OpenCode returned no output (exit code: {process.returncode})."

        output = clean_opencode_output(output)

        return output
    except FileNotFoundError:
        return "Error: 'opencode' command not found. Is it installed and in PATH?"
    except Exception as e:
        return f"Error executing command: {str(e)}"


async def run_opencode_streaming(
    prompt: str,
    on_output: callable,
    on_prompt: callable = None
) -> str:
    """Stream opencode output in real-time using PTY and background thread.
    
    Uses a pseudo-terminal (PTY) to ensure opencode flushes output immediately,
    and runs in a background thread to avoid blocking the asyncio event loop.
    
    Args:
        prompt: The prompt to send to opencode
        on_output: Callback(text: str, is_code: bool) - called for each output chunk
        on_prompt: Optional callback(question: str) -> str - called when opencode needs input
    
    Returns:
        Final cleaned output string
    """
    full_output = []
    buffer = ""
    loop = asyncio.get_event_loop()
    done_event = threading.Event()
    
    def run_in_thread():
        nonlocal buffer, full_output
        master_fd = None
        try:
            master_fd, slave_fd = pty.openpty()

            # Build command from template, substituting {prompt}
            command = CODING_ASSISTANT_CLI_COMMAND.format(prompt=prompt)
            cmd_parts = command.split()

            proc = subprocess.Popen(
                cmd_parts,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=FILE_PATH,
                close_fds=True
            )
            os.close(slave_fd)
            
            while True:
                ready, _, _ = select.select([master_fd], [], [], 0.5)
                if master_fd in ready:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError as e:
                        if e.errno == 5:
                            break
                        raise
                    if not data:
                        break
                    text = data.decode('utf-8', errors='replace')
                    buffer += text
                    
                    if is_message_boundary(buffer):
                        asyncio.run_coroutine_threadsafe(
                            on_output(buffer, is_code_block(buffer)), 
                            loop
                        ).result(timeout=1)
                        full_output.append(buffer)
                        buffer = ""
                
                if proc.poll() is not None:
                    break
            
            proc.wait()
            
        except Exception as e:
            logger.warning(f"PTY stream error: {e}")
        finally:
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            done_event.set()
    
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    
    try:
        await asyncio.wait_for(asyncio.to_thread(done_event.wait), timeout=OPENCODE_TIMEOUT)
    except asyncio.TimeoutError:
        on_output(f"Timed out after {OPENCODE_TIMEOUT}s. Try rephrasing your prompt.", False)
    
    if buffer:
        await on_output(buffer, is_code_block(buffer))
        full_output.append(buffer)
    
    return "\n".join(full_output)


async def run_think(prompt: str) -> str:
    """Runs a prompt against Groq API."""
    if not groq_configured():
        return "Error: Groq not configured. Set GROQ_API_KEY in your .env file."

    system_prompt = (
        "You are a senior software architect. Give concise, actionable "
        "advice. Focus on architecture decisions, trade-offs, and "
        "practical implementation guidance. Keep responses under 500 words "
        "unless the question demands more depth."
    )

    try:
        response = await asyncio.to_thread(groq_chat, prompt, system_prompt)
        response = strip_think_tags(response)
        return response
    except Exception as e:
        return f"Error from Qwen: {str(e)}"


async def handle_think(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /think command - route to HuggingFace AI with context continuity."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized access attempt from {user.id}")
        return

    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /think <your architecture question>")
        return

    logger.info(f"[think] Received: {prompt}")
    status_msg = await update.message.reply_text("Thinking...")

    full_prompt = prompt_builder.build_think(prompt, chat_id=chat_id)

    output = await run_think(full_prompt)
    
    tags = prompt_builder.extract_tags_from_query(prompt)
    response_tags = prompt_builder.extract_tags_from_response(output) if output else set()
    all_tags = tags | response_tags
    
    context_manager.save_context("user", prompt, channel="/think", chat_id=chat_id, user_id=user.id, tags=all_tags)
    context_manager.save_context("assistant", output[:500] if output else "", channel="/think", chat_id=chat_id, user_id=user.id, tags=response_tags)
    
    await send_chunked(update, context, status_msg, output, signature=SIGNATURE_THINK)


async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /code command - explicit route to opencode with context continuity."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized access attempt from {user.id}")
        return

    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /code <your codebase question>")
        return

    logger.info(f"[code] Received: {prompt}")
    status_msg = await update.message.reply_text("🤖 Running opencode...")

    full_prompt = prompt_builder.build_code(prompt, chat_id=chat_id)
    
    last_message_id = status_msg.message_id
    last_send_time = [0.0]
    
    async def on_output(text: str, is_code: bool):
        nonlocal last_message_id
        context.application.create_task(
            send_streaming_chunk(update, context, text, is_code, last_message_id, last_send_time)
        )
    
    output = await run_opencode_streaming(full_prompt, on_output)

    tags = prompt_builder.extract_tags_from_query(prompt)
    response_tags = prompt_builder.extract_tags_from_response(output) if output else set()
    all_tags = tags | response_tags
    
    context_manager.save_context("user", prompt, channel="/code", chat_id=chat_id, user_id=user.id, tags=all_tags)
    context_manager.save_context("assistant", output[:500] if output else "", channel="/code", chat_id=chat_id, user_id=user.id, tags=response_tags)
    
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=SIGNATURE_OPENCODE,
        parse_mode=ParseMode.MARKDOWN
    )


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


async def handle_clearcontext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clearcontext command - clear conversation memory."""
    user = update.effective_user

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        return

    old_count = context_manager.get_entry_count()
    context_manager.clear_history()
    logger.info(f"Cleared context history ({old_count} entries)")
    await update.message.reply_text(f"Cleared conversation memory ({old_count} entries removed).")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Default handler - route plain messages to opencode."""
    user = update.effective_user

    if ALLOWED_USER_ID and str(user.id) != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized access attempt from {user.id}")
        return

    prompt = update.message.text
    logger.info(f"[opencode] Received: {prompt}")

    status_msg = await update.message.reply_text("🤖 Running opencode...")

    last_message_id = status_msg.message_id
    last_send_time = [0.0]
    
    async def on_output(text: str, is_code: bool):
        nonlocal last_message_id
        context.application.create_task(
            send_streaming_chunk(update, context, text, is_code, last_message_id, last_send_time)
        )
    
    output = await run_opencode_streaming(prompt, on_output)

    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=SIGNATURE_OPENCODE,
        parse_mode=ParseMode.MARKDOWN
    )


async def send_streaming_chunk(update, context, text: str, is_code: bool, last_message_id: int, last_send_time: list) -> int:
    """Send a chunk to Telegram and return the new message ID. Rate-limited."""
    import time
    
    formatted = format_for_telegram(text, is_code)
    if not formatted:
        return last_message_id
    
    now = time.time()
    if last_send_time[0] > 0:
        elapsed = now - last_send_time[0]
        if elapsed < 0.5:
            await asyncio.sleep(0.5 - elapsed)
    
    last_send_time[0] = time.time()
    
    try:
        msg = await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=formatted,
            parse_mode=ParseMode.MARKDOWN
        )
        return msg.message_id
    except Exception as e:
        logger.warning(f"Failed to send streaming chunk: {e}")
        return last_message_id


async def send_chunked(update, context, status_msg, output, signature=None):
    """Send output in chunks respecting Telegram's 4096 char limit."""
    if signature:
        output = f"{output}\n\n{signature}"
    
    MAX_LENGTH = 4000
    chunks = [output[i:i+MAX_LENGTH] for i in range(0, len(output), MAX_LENGTH)]

    if chunks:
        try:
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=status_msg.message_id,
                text=f"```\n{chunks[0]}\n```",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("think", handle_think))
    application.add_handler(CommandHandler("code", handle_code))
    application.add_handler(CommandHandler("clearcontext", handle_clearcontext))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Talk2Code daemon started. Listening for messages...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()