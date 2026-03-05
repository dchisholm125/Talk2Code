from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import asyncio
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from logger import get_logger
from progress import ProcessingStage
from session_manager import session_manager
from telegram_handler import _edit_with_retry, is_authorized
from command_router import CommandType, ParsedCommand

_logger = get_logger()


class MessageSource(Enum):
    TELEGRAM = "telegram"
    WEB = "web"
    API = "api"


@dataclass
class IncomingMessage:
    source: MessageSource
    chat_id: int
    user_id: int
    text: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    chat_id: int
    text: str
    parse_mode: Optional[str] = None
    reply_to_message_id: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class MessageHandler:
    def __init__(self, file_path: str, telegram_edit_rate_limit: float = 0.5):
        self.file_path = file_path
        self.telegram_edit_rate_limit = telegram_edit_rate_limit
        self._allowed_user_id: Optional[str] = None
        self._progress_callbacks: List[callable] = []
    
    def set_allowed_user(self, user_id: Optional[str]) -> None:
        self._allowed_user_id = user_id
    
    def add_progress_callback(self, callback: callable) -> None:
        self._progress_callbacks.append(callback)
    
    def remove_progress_callback(self, callback: callable) -> None:
        if callback in self._progress_callbacks:
            self._progress_callbacks.remove(callback)
    
    async def handle_incoming(self, message: IncomingMessage) -> List[OutgoingMessage]:
        responses = []
        
        if message.source == MessageSource.TELEGRAM:
            if not is_authorized(message.user_id, self._allowed_user_id):
                _logger.warning(f"Unauthorized attempt from {message.user_id}")
                responses.append(OutgoingMessage(
                    chat_id=message.chat_id,
                    text=f"Unauthorized. Your ID: {message.user_id}",
                    parse_mode=ParseMode.HTML
                ))
                return responses
        
        return responses
    
    async def handle_telegram_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        
        user = update.effective_user
        chat_id = update.message.chat_id
        
        if not is_authorized(user.id, self._allowed_user_id):
            _logger.warning(f"Unauthorized attempt from {user.id}")
            return
        
        raw_text = update.message.text.strip()
        
        if not raw_text:
            return
        
        lower = raw_text.lower()
        
        if lower.startswith('#stop') or lower.startswith('#cancel'):
            await self._handle_stop(chat_id, update)
            return
        
        if lower.startswith('#restart'):
            await self._handle_restart(update, context)
            return
        
        if lower.startswith('#solo'):
            await self._handle_solo(chat_id, raw_text)
            return
        
        if lower.startswith('#code'):
            await self._handle_code(raw_text, update, context)
            return
        
        if lower.startswith('#') and not lower.startswith('#solo'):
            parts = lower.split(None, 1)
            if len(parts) >= 1:
                from assistant_manager import manager
                tag = parts[0][1:]
                if manager.get_assistant(tag):
                    await self._handle_assistant(tag, raw_text, update, context)
                    return
        
        await self._handle_brainstorm(raw_text, update, context)
    
    async def _handle_stop(self, chat_id: int, update: Update) -> None:
        from telegram_handler import handle_stop
        
        await handle_stop(chat_id)
        await update.message.reply_text("⛔ Stop requested. Terminating current action...")
    
    async def _handle_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram_handler import handle_restart
        
        status_msg = await update.message.reply_text("🔍 Checking for syntax errors before restart...")
        await handle_restart(update, context, status_msg)
    
    async def _handle_solo(self, chat_id: int, raw_text: str) -> None:
        from telegram_handler import handle_solo
        
        content = raw_text[5:].strip()
        if not content:
            return
        
        await handle_solo(chat_id, content)
    
    async def _handle_code(self, raw_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from llm_orchestrator import LLMOrchestrator, StreamOrchestrator
        from telegram_formatter import format_for_telegram, should_format
        import html
        
        chat_id = update.message.chat_id
        
        _logger.info(f"Intent detected: #code. Processing for chat {chat_id}...")
        
        extra = raw_text[5:].strip()
        window = session_manager.get_conversation_window(chat_id)
        
        if not window:
            await update.message.reply_text(
                "💭 Nothing to compress yet — chat with me first, then use #code."
            )
            return
        
        status = await update.message.reply_text(
            "<b>🧠 Compressing conversation into a coding prompt…</b>",
            parse_mode=ParseMode.HTML
        )
        
        orchestrator = LLMOrchestrator(self.file_path, self.telegram_edit_rate_limit)
        
        try:
            _logger.log_stage_start(ProcessingStage.COMPRESSING)
            coding_prompt = await orchestrator.compress_conversation(
                window, update, context, status, extra=extra
            )
        except asyncio.CancelledError:
            await _edit_with_retry(
                context.bot,
                chat_id=chat_id,
                message_id=status.message_id,
                text="⛔ Compression stopped by user.",
            )
            return
        except Exception as exc:
            await _edit_with_retry(
                context.bot,
                chat_id=chat_id,
                message_id=status.message_id,
                text=f"❌ Failed to compress conversation: {exc}",
            )
            return
        
        _logger.info(f"Compressed prompt: {coding_prompt[:100]}...")
        
        session_manager.advance_window(chat_id)
        session_manager.add_message(chat_id, "user", raw_text, solo=False)
        
        preview = coding_prompt[:600] + ("…" if len(coding_prompt) > 600 else "")
        from assistant_manager import manager
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
        
        stream_orchestrator = StreamOrchestrator(self.file_path, self.telegram_edit_rate_limit)
        
        _logger.log_stage_start(ProcessingStage.INVOKING_ASSISTANT, assistant=default_ast.name)
        await stream_orchestrator.run_streaming(
            coding_prompt, update, context, streaming_msg, agent="coder"
        )
        
        _logger.info("Finished processing #code intent.")
    
    async def _handle_assistant(self, tag: str, raw_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from llm_orchestrator import StreamOrchestrator
        from assistant_manager import manager
        
        _logger.info(f"Intent detected: Specific Assistant (#{tag})")
        
        ast = manager.get_assistant(tag)
        if ast:
            prompt = raw_text[len(tag)+1:].strip()
            if not prompt:
                await update.message.reply_text(f"Please provide a prompt for #{tag}.")
                return
            
            status = await update.message.reply_text(f"🚀 Routing to {ast.name}...")
            _logger.info(f"Starting stream targeting exact model: {ast.get_model()}")
            
            stream_orchestrator = StreamOrchestrator(self.file_path, self.telegram_edit_rate_limit)
            await stream_orchestrator.run_streaming(prompt, update, context, status, assistant=ast)
            
            _logger.info(f"Finished processing #{tag} intent.")
    
    async def _handle_brainstorm(self, raw_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from llm_orchestrator import BRAINSTORM_SYSTEM
        from assistant_manager import manager
        from telegram_formatter import format_for_telegram, should_format, get_parse_mode
        from telegram_message_utils import split_message, split_message_with_code_block, prepare_html_preview
        import asyncio
        import html
        from assistants.base import StreamEventType
        
        chat_id = update.message.chat_id
        
        _logger.info("Intent detected: Brainstorm (Plain text)")
        
        session_manager.add_message(chat_id, "user", raw_text, solo=False)
        
        window = session_manager.get_conversation_window(chat_id)
        
        streaming_msg = await update.message.reply_text("<b>🤔 Thinking...</b>", parse_mode=ParseMode.HTML)
        
        output_buffer = []
        token_count = 0
        bubble_start_time = time.time()
        
        assistant = manager.get_default_assistant()
        extra = session_manager.format_current_context_for_prompt()
        prompt_val = assistant.format_prompt(window, BRAINSTORM_SYSTEM, extra_context=extra)
        
        cmd = assistant.get_command(prompt_val, agent="plan", format_json=True)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.file_path,
            limit=2**26,
        )
        
        combined_output = ""
        reasoning_buffer = ""
        error_output = ""
        last_edit_time = 0.0
        last_event_type = None
        last_tool_name = "tool"
        bubble_start_time = 0.0
        
        async def read_stream_to_buffer(stream, is_stderr=False):
            nonlocal combined_output, reasoning_buffer, error_output, last_edit_time
            nonlocal last_event_type, bubble_start_time, last_tool_name, token_count
            
            while True:
                if session_manager.is_cancelled(streaming_msg.chat_id):
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
                    token_count += 1
                    _logger.log_token_progress(token_count, "brainstorming")
                    
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
                    if now - last_edit_time > self.telegram_edit_rate_limit:
                        last_edit_time = now
                        active_msg_body = reasoning_buffer[-3800:] if len(reasoning_buffer) > 3800 else reasoning_buffer
                        elapsed = int(now - bubble_start_time)
                        timer_str = f" <i>[Wait: {elapsed}s]</i>" if elapsed >= 10 else ""
                        escaped_body = prepare_html_preview(active_msg_body, limit=3500)
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
                    
                    _logger.debug(f"Tool call: {tool_name}")
                    
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
                if elapsed >= 10 and now - last_edit_time > 5:
                    last_edit_time = now
                    body_part = f"\n\n<code>{prepare_html_preview(reasoning_buffer, limit=3500)}</code>" if reasoning_buffer else ""
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
            session_manager.unmark_cancelled(streaming_msg.chat_id)
            raise e
        finally:
            timer_task.cancel()
        
        if process.returncode != 0:
            if assistant.is_rate_limit_error(error_output):
                if assistant.rotate_model():
                    _logger.warning(f"Retrying brainstorm with rotated model for {assistant.name}")
                    await _edit_with_retry(
                        context.bot,
                        chat_id=streaming_msg.chat_id,
                        message_id=streaming_msg.message_id,
                        text="🔄 Model rotated. Retrying brainstorming..."
                    )
        
        if "ProviderModelNotFoundError" in error_output or "Model not found" in error_output:
            _logger.error(f"Model not found error detected in brainstorm: {error_output[:200]}")
            if session_manager.record_empty_response(chat_id):
                await _edit_with_retry(
                    context.bot,
                    chat_id=chat_id,
                    message_id=streaming_msg.message_id,
                    text="❌ <b>Model Not Found</b>: The specified model is not available. "
                         "Please check your model configuration and try again.",
                    parse_mode=ParseMode.HTML
                )
                session_manager.reset_empty_response_counter(chat_id)
                return
        
        response = combined_output.strip()
        
        if not response:
            _logger.warning(f"Empty response from assistant for chat {chat_id}")
            if session_manager.record_empty_response(chat_id):
                await _edit_with_retry(
                    context.bot,
                    chat_id=chat_id,
                    message_id=streaming_msg.message_id,
                    text="❌ <b>LOOP DETECTED</b>: Assistant returned empty response 2+ times in a row. "
                         "This usually indicates a model/API issue. Please try again later or use a different model.",
                    parse_mode=ParseMode.HTML
                )
                session_manager.reset_empty_response_counter(chat_id)
                return
        else:
            session_manager.reset_empty_response_counter(chat_id)
        
        try:
            chunks = split_message_with_code_block(response)
        except Exception:
            chunks = split_message(response)
        
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
        
        ast = manager.get_default_assistant()
        await update.message.reply_text(
            f"~ {ast.name} - {ast.get_model()}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        session_manager.add_message(chat_id, "assistant", response, solo=False)
        _logger.info("Finished processing Brainstorm intent.")
    
    async def emit_progress(self, progress_data: Dict[str, Any]) -> None:
        for callback in self._progress_callbacks:
            try:
                callback(progress_data)
            except Exception as e:
                _logger.warning(f"Progress callback error: {e}")


_default_handler: Optional[MessageHandler] = None


def get_message_handler(file_path: str = ".", telegram_edit_rate_limit: float = 0.5) -> MessageHandler:
    global _default_handler
    if _default_handler is None:
        _default_handler = MessageHandler(file_path, telegram_edit_rate_limit)
    return _default_handler
