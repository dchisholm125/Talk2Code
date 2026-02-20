import os
import sys
import asyncio
import subprocess
import logging
import re
import time
import json
import html
from datetime import datetime
from collections import defaultdict
from typing import Optional
from pathlib import Path
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest, TimedOut, RetryAfter
from dotenv import load_dotenv
from telegram_formatter import format_for_telegram, should_format, get_parse_mode
from telegram_message_utils import split_message, split_message_with_code_block
from assistants.base import CodingAssistant, StreamEventType
from assistant_manager import manager

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text)

SESSION_HISTORY_PATH = Path.home() / ".voice-to-code" / "session-history.json"

def _ensure_session_dir() -> None:
    SESSION_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

def _load_session_history() -> list[dict]:
    if not SESSION_HISTORY_PATH.exists():
        return []
    try:
        with open(SESSION_HISTORY_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

def _save_session_history(sessions: list[dict]) -> None:
    _ensure_session_dir()
    with open(SESSION_HISTORY_PATH, "w") as f:
        json.dump(sessions, f, indent=2)

async def _generate_session_summary(window: list[dict]) -> str:
    lines = []
    for entry in window:
        label = "Developer" if entry["role"] == "user" else "Assistant"
        lines.append(f"{label}: {entry['content']}")
    convo_text = "\n\n".join(lines)
    summary_prompt = (
        "Given this conversation, generate a concise 2-3 sentence summary of what was accomplished "
        "or discussed. Focus on the high-level goals and outcomes, not granular details. "
        "Start with action verbs like 'Implemented', 'Fixed', 'Discussed', 'Planned', etc.\n\n"
        f"Conversation:\n{convo_text}\n\n"
        "Summary (2-3 sentences):"
    )
    result = await run_assistant(summary_prompt, agent="plan")
    return result.strip()

def _add_session_summary(summary: str) -> None:
    sessions = _load_session_history()
    sessions.append({
        "timestamp": datetime.utcnow().isoformat(),
        "summary": summary
    })
    _save_session_history(sessions)

_session_history_cache: list[dict] = []

def get_session_history() -> list[dict]:
    global _session_history_cache
    if not _session_history_cache:
        _session_history_cache = _load_session_history()
    return _session_history_cache

def format_session_history_for_prompt() -> str:
    sessions = get_session_history()
    if not sessions:
        return ""
    lines = ["Previous sessions:"]
    for i, session in enumerate(sessions, 1):
        lines.append(f"{i}. {session['summary']}")
    return "\n".join(lines)

# Load environment variables
load_dotenv()

TOKEN           = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
FILE_PATH       = os.getenv("FILE_PATH")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
HF_TOKEN        = os.getenv("HF_TOKEN")
HF_MODEL        = os.getenv("HF_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")
TELEGRAM_EDIT_RATE_LIMIT = float(os.getenv("TELEGRAM_EDIT_RATE_LIMIT", "0.5"))

# ── Per-chat conversation state ───────────────────────────────────────────────
#
# Each history entry:
#   { "role": "user" | "assistant", "content": str, "solo": bool }
#
# conversation_window_start[chat_id] is the index in that chat's history list
# where the *current* conversation window begins.  Every time #code fires, it
# advances to len(history) so the next window starts fresh after that point.
#
conversation_history: dict[int, list[dict]] = defaultdict(list)
conversation_window_start: dict[int, int]   = defaultdict(int)
cancelled_sessions: dict[int, bool]          = {}

def _init_llm() -> None:
    default_ast = manager.get_default_assistant()
    logger.info(f"Using {default_ast.name} as default coding assistant")


# ── Conversation helpers ──────────────────────────────────────────────────────

BRAINSTORM_SYSTEM = (
    "You are a collaborative thinking partner and software architect helping a developer "
    "brainstorm, plan, and refine their ideas. Engage thoughtfully, ask clarifying questions, "
    "and help sharpen concepts. You are NOT writing code right now — this is a shared thinking "
    "space. Keep responses concise and conversational. "
    "Reference the conversation above when relevant to maintain context across messages."
)

COMPRESS_SYSTEM = (
    "You are a technical writer specialising in software specifications. "
    "Given a brainstorming conversation between a developer and their AI assistant, "
    "synthesise it into a single, clear, and actionable implementation prompt for a coding "
    "assistant. Include all relevant technical details, constraints, and goals discussed. "
    "Write it as a direct, comprehensive instruction. No preamble."
)




async def _brainstorm_response(window: list[dict], update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    streaming_msg = await update.message.reply_text("⏳ Thinking...")
    output_buffer = []

    async def _capture_stream(msg):
        assistant = manager.get_default_assistant()
        extra = format_session_history_for_prompt()
        prompt_val = assistant.format_prompt(window, BRAINSTORM_SYSTEM, extra_context=extra)
        start_time = time.time()
        while True:
            cmd = assistant.get_command(prompt_val, agent="plan", format_json=True)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FILE_PATH,
            )

            combined_output = ""
            reasoning_buffer = ""
            error_output = ""
            last_edit_time = 0.0
            
            active_msg = msg
            active_msg_header = "<b>⏳ Starting...</b>"
            active_msg_body = ""
            last_event_type = None
            last_tool_name = "tool"
            bubble_start_time = time.time()

            async def read_stream_to_buffer(stream, is_stderr=False):
                nonlocal combined_output, reasoning_buffer, error_output, last_edit_time
                nonlocal active_msg, active_msg_header, active_msg_body, last_event_type, bubble_start_time, last_tool_name
                
                while True:
                    if cancelled_sessions.get(msg.chat_id):
                        raise asyncio.CancelledError("User requested stop.")

                    line = await stream.readline()
                    if not line:
                        break
                    
                    line_decoded = line.decode("utf-8").strip()
                    if is_stderr:
                        error_output += line_decoded + "\n"

                    event = assistant.parse_line(line_decoded)
                    if not event:
                        continue

                    if event.type == StreamEventType.REASONING:
                        if last_event_type != StreamEventType.REASONING:
                            active_msg = await update.message.reply_text("<b>🤔 Thinking...</b>", parse_mode=ParseMode.HTML)
                            active_msg_header = "<b>🤔 Thinking...</b>"
                            active_msg_body = ""
                            last_event_type = StreamEventType.REASONING
                            last_edit_time = time.time()
                            bubble_start_time = time.time()

                        if event.content:
                            reasoning_buffer += event.content
                        now = time.time()
                        if now - last_edit_time > TELEGRAM_EDIT_RATE_LIMIT:
                            last_edit_time = now
                            preview = reasoning_buffer[-3800:] if len(reasoning_buffer) > 3800 else reasoning_buffer
                            active_msg_body = preview
                            elapsed = int(now - bubble_start_time)
                            timer_str = f" <i>[Wait: {elapsed}s]</i>" if elapsed >= 30 else ""
                            escaped_body = html.escape(active_msg_body)
                            await _edit_with_retry(
                                context.bot,
                                chat_id=active_msg.chat_id,
                                message_id=active_msg.message_id,
                                text=f"{active_msg_header}{timer_str}\n\n<code>{escaped_body}</code>",
                                parse_mode=ParseMode.HTML
                            )
                    elif event.type == StreamEventType.TEXT:
                        if last_event_type != StreamEventType.TEXT and last_event_type is not None:
                             active_msg = await update.message.reply_text("<b>✍️ Writing answer...</b>", parse_mode=ParseMode.HTML)
                             active_msg_header = "<b>✍️ Writing answer...</b>"
                             active_msg_body = ""
                             last_event_type = StreamEventType.TEXT
                             last_edit_time = time.time()
                             bubble_start_time = time.time()
                             
                        if event.content:
                            combined_output += event.content
                    elif event.type == StreamEventType.TOOL_USE:
                        tool_name = event.metadata.get("name", "tool")
                        last_tool_name = tool_name
                        params = event.metadata.get("input", "")
                        
                        active_msg_header = f"<b>🛠️ Calling:</b> <code>{html.escape(tool_name)}</code>"
                        active_msg_body = f"Requested with: \n<code>{html.escape(params)}</code>" if params else ""
                        active_msg = await update.message.reply_text(
                            active_msg_header + (f"\n\n{active_msg_body}" if active_msg_body else ""),
                            parse_mode=ParseMode.HTML
                        )
                        last_event_type = StreamEventType.TOOL_USE
                        bubble_start_time = time.time()
                    elif event.type == StreamEventType.TOOL_RESULT:
                        if event.content:
                            res_text = event.content
                            combined_output += f"\n[Tool Result]: {res_text}\n"
                            
                            active_msg_header = f"<b>📋 Result from:</b> <code>{html.escape(last_tool_name)}</code>"
                            active_msg_body = res_text[:800] + ("..." if len(res_text)>800 else "")
                            active_msg = await update.message.reply_text(
                                f"{active_msg_header}\n\n<pre>{html.escape(active_msg_body)}</pre>",
                                parse_mode=ParseMode.HTML
                            )
                            last_event_type = StreamEventType.TOOL_RESULT
                            bubble_start_time = time.time()

            async def heartbeat():
                nonlocal last_edit_time
                while process.returncode is None:
                    await asyncio.sleep(5)
                    now = time.time()
                    elapsed = int(now - bubble_start_time)
                    if elapsed >= 30 and now - last_edit_time > 5:
                        last_edit_time = now
                        body_part = f"\n\n<code>{html.escape(active_msg_body)}</code>" if active_msg_body else ""
                        await _edit_with_retry(
                            context.bot,
                            chat_id=active_msg.chat_id,
                            message_id=active_msg.message_id,
                            text=f"{active_msg_header} <i>[Wait: {elapsed}s]</i>{body_part}",
                            parse_mode=ParseMode.HTML
                        )

            timer_task = asyncio.create_task(heartbeat())
            try:
                await asyncio.gather(
                    read_stream_to_buffer(process.stdout),
                    read_stream_to_buffer(process.stderr, is_stderr=True)
                )
                await process.wait()
            except (asyncio.CancelledError, Exception) as e:
                process.terminate()
                cancelled_sessions[msg.chat_id] = False
                raise e
            finally:
                timer_task.cancel()

            if process.returncode != 0:
                if assistant.is_rate_limit_error(error_output):
                    if assistant.rotate_model():
                        logger.warning(f"Retrying brainstorm with rotated model for {assistant.name}")
                        await _edit_with_retry(
                            context.bot,
                            chat_id=msg.chat_id,
                            message_id=msg.message_id,
                            text="🔄 Model rotated. Retrying brainstorming..."
                        )
                        continue
            
            return combined_output.strip()

    try:
        response = await _capture_stream(streaming_msg)
        await _reply_chunked(update, response)
        
        if should_format():
            response = format_for_telegram(response)
        
        return response
    except Exception as e:
        await _edit_with_retry(
            context.bot,
            chat_id=streaming_msg.chat_id,
            message_id=streaming_msg.message_id,
            text=f"⚠️ Error: {e}"
        )
        return f"⚠️ LLM error: {e}"


async def _compress_to_prompt(window: list[dict], update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg, extra: str = "") -> str:
    """Compress the conversation window into one actionable coding prompt and stream it."""
    assistant = manager.get_default_assistant()
    convo_text = assistant.format_prompt(window, COMPRESS_SYSTEM, extra_context=extra)
    
    while True:
        cmd = assistant.get_command(convo_text, agent="plan", format_json=True)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FILE_PATH,
            )

            output_buffer = ""
            reasoning_buffer = ""
            error_output = ""
            last_edit_time = time.time()
            last_activity = time.time()
            
            active_msg_header = "<b>🧠 Compressing conversation...</b>"
            active_msg_body = ""
            bubble_start_time = time.time()
            
            async def read_stream_with_timeout(stream, is_stderr=False):
                nonlocal output_buffer, reasoning_buffer, error_output, last_edit_time, last_activity
                nonlocal active_msg_header, active_msg_body, bubble_start_time
                
                while True:
                    if cancelled_sessions.get(status_msg.chat_id):
                        raise asyncio.CancelledError("User requested stop.")

                    try:
                        line = await asyncio.wait_for(stream.readline(), timeout=5.0)
                    except asyncio.TimeoutError:
                        if time.time() - last_activity > 45.0:
                            raise TimeoutError("Assistant process hung for too long.")
                        continue
                        
                    if not line:
                        break
                    
                    last_activity = time.time()
                    line_decoded = line.decode("utf-8").strip()
                    if is_stderr:
                        error_output += line_decoded + "\n"
                    
                    event = assistant.parse_line(line_decoded)
                    if not event:
                        continue

                    if event.type == StreamEventType.REASONING:
                        if event.content:
                            reasoning_buffer += event.content
                        now = time.time()
                        if now - last_edit_time > TELEGRAM_EDIT_RATE_LIMIT:
                            last_edit_time = now
                            preview = reasoning_buffer[-3800:] if len(reasoning_buffer) > 3800 else reasoning_buffer
                            active_msg_body = preview
                            elapsed = int(now - bubble_start_time)
                            timer_str = f" <i>[Wait: {elapsed}s]</i>" if elapsed >= 30 else ""
                            escaped_body = html.escape(active_msg_body)
                            await _edit_with_retry(
                                context.bot,
                                chat_id=status_msg.chat_id,
                                message_id=status_msg.message_id,
                                text=f"{active_msg_header}{timer_str}\n\n<code>{escaped_body}</code>",
                                parse_mode=ParseMode.HTML
                            )
                    elif event.type == StreamEventType.TEXT:
                        if event.content:
                            output_buffer += event.content
                        now = time.time()
                        if now - last_edit_time > TELEGRAM_EDIT_RATE_LIMIT:
                            last_edit_time = now
                            preview = output_buffer[-3800:] if len(output_buffer) > 3800 else output_buffer
                            active_msg_body = preview
                            elapsed = int(now - bubble_start_time)
                            timer_str = f" <i>[Wait: {elapsed}s]</i>" if elapsed >= 30 else ""
                            escaped_body = html.escape(active_msg_body)
                            await _edit_with_retry(
                                context.bot,
                                chat_id=status_msg.chat_id,
                                message_id=status_msg.message_id,
                                text=f"{active_msg_header}{timer_str}\n\n<code>{escaped_body}</code>",
                                parse_mode=ParseMode.HTML
                            )

            async def heartbeat():
                nonlocal last_edit_time
                while process.returncode is None:
                    await asyncio.sleep(5)
                    now = time.time()
                    elapsed = int(now - bubble_start_time)
                    if elapsed >= 30 and now - last_edit_time > 5:
                        last_edit_time = now
                        body_block = f"\n\n<code>{html.escape(active_msg_body)}</code>" if active_msg_body else ""
                        await _edit_with_retry(
                            context.bot,
                            chat_id=status_msg.chat_id,
                            message_id=status_msg.message_id,
                            text=f"{active_msg_header} <i>[Wait: {elapsed}s]</i>{body_block}",
                            parse_mode=ParseMode.HTML
                        )

            timer_task = asyncio.create_task(heartbeat())
            try:
                await asyncio.gather(
                    read_stream_with_timeout(process.stdout, is_stderr=False),
                    read_stream_with_timeout(process.stderr, is_stderr=True)
                )
                await process.wait()
            except (asyncio.CancelledError, Exception) as e:
                process.terminate()
                cancelled_sessions[status_msg.chat_id] = False
                raise e
            finally:
                timer_task.cancel()
            
            try:
                await asyncio.wait_for(process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                 process.terminate()

            if process.returncode != 0:
                if assistant.is_rate_limit_error(error_output):
                    if assistant.rotate_model():
                        logger.warning(f"Retrying compress with rotated model for {assistant.name}")
                        await _edit_with_retry(
                            context.bot,
                            chat_id=status_msg.chat_id,
                            message_id=status_msg.message_id,
                            text=f"🔄 Model rotated. Retrying compression..."
                        )
                        continue
            
            return output_buffer.strip()
        except Exception as e:
            logger.error(f"Error compressing: {e}")
            raise e


# ── assistant runner ──────────────────────────────────────────────────────────

async def run_assistant(prompt: str, assistant: Optional[CodingAssistant] = None, agent: str = "coder") -> str:
    """Run an assistant command and return its output without streaming."""
    if assistant is None:
        assistant = manager.get_default_assistant()
    
    while True:
        cmd = assistant.get_command(prompt, agent=agent)
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                cwd=FILE_PATH,
            )
            
            # Check for error codes that trigger rotation
            if result.returncode != 0:
                if assistant.is_rate_limit_error(result.stderr):
                    if assistant.rotate_model():
                        logger.warning(f"Retrying with rotated model for {assistant.name}")
                        continue

            output = result.stdout
            if result.stderr:
                output += f"\n\nSTDERR:\n{result.stderr}"
            output = strip_ansi(output)
            return output.strip() or f"Assistant {assistant.name} executed but returned no output."
        except subprocess.TimeoutExpired:
            return f"Error: {assistant.name} timed out after 300 seconds."
        except FileNotFoundError:
            return f"Error: Command '{cmd[0]}' not found — is it installed and on PATH?"
        except Exception as e:
            return f"Error: {e}"


async def run_assistant_stream(prompt: str, update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg, assistant: Optional[CodingAssistant] = None, agent: str = "coder") -> None:
    """Run an assistant command and stream its output to a telegram message."""
    if assistant is None:
        assistant = manager.get_default_assistant()
    
    chat_id = status_msg.chat_id
    if chat_id in cancelled_sessions:
        cancelled_sessions[chat_id] = False
        
    while True:
        cmd = assistant.get_command(prompt, agent=agent, format_json=True)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FILE_PATH,
            )

            output_buffer = ""
            last_edit_time = 0.0
            error_output = ""
            
            active_msg = status_msg
            active_msg_header = "<b>⏳ Starting Assistant...</b>"
            active_msg_body = ""
            last_event_type = None
            last_tool_name = "tool"
            bubble_start_time = time.time()
            
            async def read_stream_with_timeout(stream, is_stderr=False):
                nonlocal output_buffer, last_edit_time, error_output, last_activity
                nonlocal active_msg, active_msg_header, active_msg_body, last_event_type, bubble_start_time, last_tool_name
                
                while True:
                    if cancelled_sessions.get(status_msg.chat_id):
                        raise asyncio.CancelledError("User requested stop.")
                    try:
                        line = await asyncio.wait_for(stream.readline(), timeout=5.0)
                    except asyncio.TimeoutError:
                        if time.time() - last_activity > 45.0: # 45 sec absolute timeout
                            raise TimeoutError("Assistant process hung for too long.")
                        continue # just a read timeout, check process again
                        
                    if not line:
                        break
                    
                    last_activity = time.time()
                    
                    line_decoded = line.decode("utf-8").strip()
                    if is_stderr:
                        error_output += line_decoded + "\n"
                    
                    if cancelled_sessions.get(chat_id):
                        process.terminate()
                        await _edit_with_retry(
                            context.bot,
                            chat_id=status_msg.chat_id,
                            message_id=status_msg.message_id,
                            text="Session cancelled."
                        )
                        cancelled_sessions[chat_id] = False
                        return

                    event = assistant.parse_line(line_decoded)
                    if not event:
                        continue

                    if event.type == StreamEventType.REASONING:
                        if last_event_type != StreamEventType.REASONING:
                             active_msg = await update.message.reply_text("<b>🤔 Thinking...</b>", parse_mode=ParseMode.HTML)
                             active_msg_header = "<b>🤔 Thinking...</b>"
                             active_msg_body = ""
                             last_event_type = StreamEventType.REASONING
                             last_edit_time = time.time()
                             bubble_start_time = time.time()

                        if event.content:
                            output_buffer += event.content
                        now = time.time()
                        if now - last_edit_time > TELEGRAM_EDIT_RATE_LIMIT:
                            last_edit_time = now
                            elapsed = int(now - bubble_start_time)
                            timer_str = f" <i>[Wait: {elapsed}s]</i>" if elapsed >= 30 else ""
                            display_text = output_buffer[-3800:] if len(output_buffer) > 3800 else output_buffer
                            active_msg_body = display_text
                            target_body = html.escape(display_text)
                            await _edit_with_retry(
                                context.bot,
                                chat_id=active_msg.chat_id,
                                message_id=active_msg.message_id,
                                text=f"{active_msg_header}{timer_str}\n\n<code>{target_body}</code>",
                                parse_mode=ParseMode.HTML
                            )
                    elif event.type == StreamEventType.TEXT:
                        if last_event_type != StreamEventType.TEXT and last_event_type is not None:
                             active_msg = await update.message.reply_text("<b>✍️ Writing answer...</b>", parse_mode=ParseMode.HTML)
                             active_msg_header = "<b>✍️ Writing answer...</b>"
                             active_msg_body = ""
                             last_event_type = StreamEventType.TEXT
                             last_edit_time = time.time()
                             bubble_start_time = time.time()

                        if event.content:
                            output_buffer += event.content
                    elif event.type == StreamEventType.TOOL_USE:
                        name = event.metadata.get("name", "tool")
                        last_tool_name = name
                        inp = event.metadata.get("input", "")
                        
                        active_msg_header = f"<b>🛠️ Calling:</b> <code>{html.escape(name)}</code>"
                        active_msg_body = f"Params: \n<code>{html.escape(inp)}</code>" if inp else ""
                        active_msg = await update.message.reply_text(
                            f"{active_msg_header}\n\n{active_msg_body}",
                            parse_mode=ParseMode.HTML
                        )
                        last_event_type = StreamEventType.TOOL_USE
                        bubble_start_time = time.time()
                    elif event.type == StreamEventType.TOOL_RESULT:
                        if event.content:
                            res_text = event.content
                            active_msg_header = f"<b>📋 Result from:</b> <code>{html.escape(last_tool_name)}</code>"
                            active_msg_body = res_text[:800] + ("..." if len(res_text) > 800 else "")
                            await update.message.reply_text(
                                f"{active_msg_header}\n\n<pre>{html.escape(active_msg_body)}</pre>",
                                parse_mode=ParseMode.HTML
                            )
                        last_event_type = StreamEventType.TOOL_RESULT
                        bubble_start_time = time.time()

            start_time = time.time()
            last_activity = time.time()

            async def heartbeat():
                nonlocal last_edit_time
                while process.returncode is None:
                    await asyncio.sleep(5)
                    now = time.time()
                    elapsed = int(now - bubble_start_time)
                    if elapsed >= 30 and now - last_edit_time > 5:
                        last_edit_time = now
                        body_block = f"\n\n<code>{html.escape(active_msg_body)}</code>" if active_msg_body else ""
                        await _edit_with_retry(
                            context.bot,
                            chat_id=active_msg.chat_id,
                            message_id=active_msg.message_id,
                            text=f"{active_msg_header} <i>[Wait: {elapsed}s]</i>{body_block}",
                            parse_mode=ParseMode.HTML
                        )

            timer_task = asyncio.create_task(heartbeat())
            try:
                await asyncio.gather(
                    read_stream_with_timeout(process.stdout, is_stderr=False),
                    read_stream_with_timeout(process.stderr, is_stderr=True)
                )
                await process.wait()
            finally:
                timer_task.cancel()
            
            # Additional safety to catch the process if it exited but reader loops closed
            try:
                await asyncio.wait_for(process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                 process.terminate()
                 raise TimeoutError("Assistant process failed to exit cleanly.")

            # Check for error codes that trigger rotation
            if process.returncode != 0:
                if assistant.is_rate_limit_error(error_output):
                    if assistant.rotate_model():
                        logger.warning(f"Retrying stream with rotated model for {assistant.name}")
                        await _edit_with_retry(
                            context.bot,
                            chat_id=status_msg.chat_id,
                            message_id=status_msg.message_id,
                            text=f"🔄 Model rotated due to rate limit/error. Retrying..."
                        )
                        continue

            if output_buffer.strip():
                final_output = format_for_telegram(output_buffer.strip()) if should_format() else output_buffer.strip()
                await _edit_with_retry(
                    context.bot,
                    chat_id=status_msg.chat_id,
                    message_id=status_msg.message_id,
                    text=f"<b>✅ {assistant.name}</b> finished execution.",
                    parse_mode=ParseMode.HTML
                )
                await _reply_chunked(update, final_output, code_block=True)
                
                # Add the final UX bubble
                await update.message.reply_text(
                    f"<b>✓</b> <i>{assistant.name} • {assistant.get_model()}</i>",
                    parse_mode=ParseMode.HTML
                )
            else:
                 await _edit_with_retry(
                    context.bot,
                    chat_id=status_msg.chat_id,
                    message_id=status_msg.message_id,
                    text=f"{assistant.name} executed but returned no output."
                )
            break

        except FileNotFoundError:
            await _edit_with_retry(
                context.bot,
                chat_id=status_msg.chat_id,
                message_id=status_msg.message_id,
                text=f"Error: Command not found — is it installed and on PATH?"
            )
            break
        except Exception as e:
            logger.error(f"Error in run_assistant_stream: {e}")
            break


# ── Telegram utilities ────────────────────────────────────────────────────────

async def _reply_chunked(
    update: Update,
    text: str,
    code_block: bool = False,
) -> None:
    """Send a potentially long reply, splitting at Telegram's 4096-char limit.
    
    Uses threaded/linked messages: first chunk replies to user's message,
    subsequent chunks reply to the previous chunk.
    """
    if not text or not text.strip():
        await update.message.reply_text("(empty)", parse_mode=get_parse_mode())
        return
    
    try:
        if code_block:
            chunks = split_message_with_code_block(text)
        else:
            formatted_text = format_for_telegram(text) if should_format() else text
            chunks = split_message(formatted_text)
        
        reply_to_message_id = update.message.message_id
        
        for i, chunk in enumerate(chunks):
            sent_msg = await update.message.reply_text(
                chunk,
                parse_mode=get_parse_mode(),
                reply_to_message_id=reply_to_message_id
            )
            reply_to_message_id = sent_msg.message_id
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)
    except Exception as e:
        logger.error(f"Error sending message chunk: {e}")
        await update.message.reply_text(f"Error: {e}", parse_mode=get_parse_mode())


async def _edit_with_retry(bot, chat_id: int, message_id: int, text: str, **kwargs) -> bool:
    """Edit a message with retry-on-failure for TimedOut/RetryAfter errors.
    
    Returns True if edit succeeded, False if it failed after retry.
    """
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            **kwargs
        )
        return True
    except (TimedOut, RetryAfter) as e:
        logger.warning(f"Edit message retry after {e}: {e}")
        await asyncio.sleep(0.5)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                **kwargs
            )
            return True
        except Exception as retry_err:
            logger.error(f"Edit message failed after retry: {retry_err}")
            return False
    except Exception as e:
        logger.error(f"Edit message failed: {e}")
        return False


def _is_authorized(user_id: int) -> bool:
    return not ALLOWED_USER_ID or str(user_id) == ALLOWED_USER_ID


# ── Command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("User %s (%s) started the bot.", user.id, user.username)

    if not _is_authorized(user.id):
        await update.message.reply_html(f"Unauthorized. Your ID: {user.id}")
        return

    await update.message.reply_html(
        f"Hey {user.mention_html()}! 💬 <b>Chatroom mode active.</b>\n\n"
        f"Just talk — I'll brainstorm with you.\n\n"
        f"<code>#solo your thoughts</code> — monologue mode; I'll listen silently.\n"
        f"<code>#code [optional focus]</code> — compress this conversation into a "
        f"coding task and hand it off to opencode.\n\n"
        f"<code>#stop</code> — cancel an ongoing session.\n\n"
        f"/clear — wipe the slate and start a fresh conversation.\n"
        f"/cancel — cancel an ongoing #code session."
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset conversation history for this chat."""
    if not _is_authorized(update.effective_user.id):
        return

    chat_id = update.message.chat_id
    conversation_history[chat_id].clear()
    conversation_window_start[chat_id] = 0
    await update.message.reply_text("🧹 Conversation cleared. Fresh start!")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel an ongoing #code session."""
    if not _is_authorized(update.effective_user.id):
        return

    chat_id = update.message.chat_id
    cancelled_sessions[chat_id] = True
    await update.message.reply_text("⛔ Session cancelled.")


# ── Core message handler ───────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    if not _is_authorized(user.id):
        logger.warning("Unauthorized attempt from %s", user.id)
        return

    chat_id  = update.message.chat_id
    raw_text = update.message.text.strip()
    lower    = raw_text.lower()

    # ── #stop / #cancel ──────────────────────────────────────────────────────
    if lower.startswith("#stop") or lower.startswith("#cancel"):
        cancelled_sessions[chat_id] = True
        await update.message.reply_text("⛔ Stop requested. Terminating current action...")
        return

    # ── #restart ──────────────────────────────────────────────────────────────
    if lower.startswith("#restart"):
        status_msg = await update.message.reply_text("🔍 Checking for syntax errors before restart...")
        
        import py_compile
        error_files = []
        for py_file in Path("src").glob("*.py"):
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                error_files.append(f"{py_file.name}: {str(e)}")
        
        if error_files:
            error_list = "\n".join(error_files)
            await _edit_with_retry(
                context.bot,
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"❌ Restart aborted! Syntax errors detected:\n\n{error_list}"
            )
            return

        await _edit_with_retry(
            context.bot,
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text="🔄 Syntax check passed! Restarting daemon... Be right back!"
        )
        
        # Clean any old restart args, then add the current ones
        argv = [arg for arg in sys.argv if arg != '--restart-chat-id' and not str(arg).replace('-', '').isdigit()]
        argv.extend(['--restart-chat-id', str(chat_id)])
        
        # Replaces the current process with a new one
        os.execv(sys.executable, ['python'] + argv)
        return

    # ── #solo ─────────────────────────────────────────────────────────────────
    # User is monologuing — log it to history, stay completely silent.
    if lower.startswith("#solo"):
        content = raw_text[5:].strip()
        if not content:
            return
        logger.info("[SOLO] %s", content)
        conversation_history[chat_id].append(
            {"role": "user", "content": content, "solo": True}
        )
        # Intentionally no reply.
        return

    # ── #code ─────────────────────────────────────────────────────────────────
    # Compress the current conversation window → opencode.
    if lower.startswith("#code"):
        logger.info(f"[ROUTER] Intent detected: #code. Processing for chat {chat_id}...")
        extra        = raw_text[5:].strip()
        window_start = conversation_window_start[chat_id]
        window       = conversation_history[chat_id][window_start:]

        if not window:
            await update.message.reply_text(
                "💭 Nothing to compress yet — chat with me first, then use #code."
            )
            return

        status = await update.message.reply_text(
            "<b>🧠 Compressing conversation into a coding prompt…</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            coding_prompt = await _compress_to_prompt(window, update, context, status, extra=extra)
        except (asyncio.CancelledError, Exception) as exc:
            text = "⛔ Compression stopped by user." if isinstance(exc, asyncio.CancelledError) else f"❌ Failed to compress conversation: {exc}"
            await _edit_with_retry(
                context.bot,
                chat_id=chat_id,
                message_id=status.message_id,
                text=text,
            )
            return

        logger.info("[CODE] Compressed prompt: %s", coding_prompt)

        # Advance the window *before* appending the #code event so the next
        # conversation starts cleanly after this point.
        conversation_window_start[chat_id] = len(conversation_history[chat_id])
        conversation_history[chat_id].append(
            {"role": "user", "content": raw_text, "solo": False}
        )

        preview = coding_prompt[:600] + ("…" if len(coding_prompt) > 600 else "")
        default_ast = manager.get_default_assistant()
        await _edit_with_retry(
            context.bot,
            chat_id=chat_id,
            message_id=status.message_id,
            text=f"<b>📋 Prompt sent to {default_ast.name}:</b>\n\n<pre>{html.escape(preview)}</pre>\n\n⏳ <b>Running…</b>",
            parse_mode=ParseMode.HTML,
        )

        code_window = window
        streaming_msg = await update.message.reply_text("⏳ Assistant output starting...")
        await run_assistant_stream(coding_prompt, update, context, streaming_msg, agent="coder")

        try:
            summary = await _generate_session_summary(code_window)
            _add_session_summary(summary)
            logger.info("[CODE] Session summary generated successfully.")
        except Exception as e:
            logger.warning("Failed to generate session summary: %s", e)
        logger.info("[ROUTER] Finished processing #code intent.")
        return

    # ── hashtag specific assistants ──────────────────────────────────────────
    # If a message starts with #<assistant_name>, route directly to that assistant.
    if lower.startswith("#") and not lower.startswith("#solo") and not lower.startswith("#code") and not lower.startswith("#restart"):
        tag = lower.split()[0][1:]
        logger.info(f"[ROUTER] Intent detected: Specific Assistant (#{tag}). Processing...")
        ast = manager.get_assistant(tag)
        if ast:
            prompt = raw_text[len(tag)+1:].strip()
            if not prompt:
                await update.message.reply_text(f"Please provide a prompt for #{tag}.")
                return
            
            status = await update.message.reply_text(f"🚀 Routing to {ast.name}...")
            logger.info(f"[{tag.upper()}] Starting stream targeting exact model: {ast.get_model()}")
            await run_assistant_stream(prompt, update, context, status, assistant=ast)
            logger.info(f"[ROUTER] Finished processing #{tag} intent.")
            return

    # ── Brainstorm mode ───────────────────────────────────────────────────────
    # Normal chat — accumulate history and respond conversationally.
    logger.info(f"[ROUTER] Intent detected: Brainstorm (Plain text). Processing...")
    conversation_history[chat_id].append(
        {"role": "user", "content": raw_text, "solo": False}
    )

    window_start = conversation_window_start[chat_id]
    window       = conversation_history[chat_id][window_start:]

    try:
        response = await _brainstorm_response(window, update, context)
        # Add final UX bubble for brainstorming as well
        ast = manager.get_default_assistant()
        await update.message.reply_text(
            f"~ {ast.name} - {ast.get_model()}",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as exc:
        response = f"⚠️ LLM error: {exc}"

    conversation_history[chat_id].append(
        {"role": "assistant", "content": response, "solo": False}
    )
    logger.info("[ROUTER] Finished processing Brainstorm intent.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in .env")
        return

    _init_llm()
    get_session_history()

    async def post_init(application: Application):
        if '--restart-chat-id' in sys.argv:
            idx = sys.argv.index('--restart-chat-id')
            if idx + 1 < len(sys.argv):
                try:
                    chat_id = int(sys.argv[idx+1])
                    await application.bot.send_message(chat_id=chat_id, text="🚀 Hello, we're back online and ready to receive messages!")
                except Exception as e:
                    logger.error(f"Failed to send restart message: {e}")

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
