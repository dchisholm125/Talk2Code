from enum import Enum
from typing import Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass
import re

from logger import get_logger
from assistant_manager import manager
from assistants.base import CodingAssistant

_logger = get_logger()


class CommandType(Enum):
    CODE = "code"
    SOLO = "solo"
    STOP = "stop"
    CANCEL = "cancel"
    RESTART = "restart"
    BRAINSTORM = "brainstorm"
    ASSISTANT = "assistant"


@dataclass
class ParsedCommand:
    command_type: CommandType
    raw_text: str
    content: str
    assistant_tag: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class CommandParser:
    COMMAND_PATTERNS = {
        CommandType.CODE: re.compile(r'^#code\s*(.*)$', re.IGNORECASE),
        CommandType.SOLO: re.compile(r'^#solo\s*(.*)$', re.IGNORECASE),
        CommandType.STOP: re.compile(r'^#stop\s*(.*)$', re.IGNORECASE),
        CommandType.CANCEL: re.compile(r'^#cancel\s*(.*)$', re.IGNORECASE),
        CommandType.RESTART: re.compile(r'^#restart\s*(.*)$', re.IGNORECASE),
    }
    
    def parse(self, text: str) -> ParsedCommand:
        raw_text = text.strip()
        lower = raw_text.lower()
        
        for cmd_type, pattern in self.COMMAND_PATTERNS.items():
            match = pattern.match(lower)
            if match:
                content = match.group(1).strip() if match.group(1) else ""
                return ParsedCommand(
                    command_type=cmd_type,
                    raw_text=raw_text,
                    content=content,
                    metadata={'original_content': content}
                )
        
        if lower.startswith('#') and not lower.startswith('#solo'):
            parts = lower.split(None, 1)
            if len(parts) >= 1:
                tag = parts[0][1:]
                if manager.get_assistant(tag):
                    content = text[len(parts[0]):].strip() if len(parts) > 1 else ""
                    return ParsedCommand(
                        command_type=CommandType.ASSISTANT,
                        raw_text=raw_text,
                        content=content,
                        assistant_tag=tag,
                        metadata={'assistant_tag': tag}
                    )
        
        return ParsedCommand(
            command_type=CommandType.BRAINSTORM,
            raw_text=raw_text,
            content=raw_text
        )


class CommandRouter:
    def __init__(self):
        self._parser = CommandParser()
        self._handlers: Dict[CommandType, Callable[[ParsedCommand, Any], Awaitable[Any]]] = {}
    
    def register_handler(self, command_type: CommandType, handler: Callable[[ParsedCommand, Any], Awaitable[Any]]) -> None:
        self._handlers[command_type] = handler
    
    async def route(self, command: ParsedCommand, context: Any) -> Any:
        handler = self._handlers.get(command.command_type)
        
        if handler:
            return await handler(command, context)
        
        _logger.warning(f"No handler registered for command type: {command.command_type}")
        return None
    
    def parse(self, text: str) -> ParsedCommand:
        return self._parser.parse(text)
    
    def parse_and_route(self, text: str, context: Any) -> Awaitable[Any]:
        command = self.parse(text)
        return self.route(command, context)


class CommandExecutor:
    def __init__(self, file_path: str, telegram_edit_rate_limit: float = 0.5):
        self.file_path = file_path
        self.telegram_edit_rate_limit = telegram_edit_rate_limit
    
    async def execute_code_command(
        self,
        command: ParsedCommand,
        update: Any,
        context: Any,
        extra: str = ""
    ) -> Dict[str, Any]:
        from llm_orchestrator import LLMOrchestrator, StreamOrchestrator
        from telegram_handler import _edit_with_retry
        from session_manager import session_manager
        from telegram.constants import ParseMode
        import html
        import asyncio
        
        chat_id = update.message.chat_id
        window = session_manager.get_conversation_window(chat_id)
        
        if not window:
            return {
                'success': False,
                'error': 'Nothing to compress yet — chat with me first, then use #code.'
            }
        
        content = command.content or extra
        
        status = await update.message.reply_text(
            "<b>🧠 Compressing conversation into a coding prompt…</b>",
            parse_mode=ParseMode.HTML
        )
        
        orchestrator = LLMOrchestrator(self.file_path, self.telegram_edit_rate_limit)
        
        try:
            _logger.log_stage_start('compressing')
            coding_prompt = await orchestrator.compress_conversation(
                window, update, context, status, extra=content
            )
        except asyncio.CancelledError:
            await _edit_with_retry(
                context.bot,
                chat_id=chat_id,
                message_id=status.message_id,
                text="⛔ Compression stopped by user.",
            )
            return {'success': False, 'cancelled': True}
        except Exception as exc:
            await _edit_with_retry(
                context.bot,
                chat_id=chat_id,
                message_id=status.message_id,
                text=f"❌ Failed to compress conversation: {exc}",
            )
            return {'success': False, 'error': str(exc)}
        
        _logger.info(f"Compressed prompt: {coding_prompt[:100]}...")
        
        session_manager.advance_window(chat_id)
        session_manager.add_message(chat_id, "user", command.raw_text, solo=False)
        
        preview = coding_prompt[:600] + ("…" if len(coding_prompt) > 600 else "")
        default_ast = manager.get_default_assistant()
        await _edit_with_retry(
            context.bot,
            chat_id=chat_id,
            message_id=status.message_id,
            text=f"<b>📋 Prompt sent to {default_ast.name}:</b>\n\n<pre>{html.escape(preview)}</pre>\n\n⏳ <b>Running…</b>",
            parse_mode=ParseMode.HTML,
        )
        
        streaming_msg = await update.message.reply_text("⏳ Assistant output starting...")
        
        stream_orchestrator = StreamOrchestrator(self.file_path, self.telegram_edit_rate_limit)
        
        _logger.log_stage_start('invoking_assistant', assistant=default_ast.name)
        await stream_orchestrator.run_streaming(
            coding_prompt, update, context, streaming_msg, agent="coder"
        )
        
        return {'success': True, 'streaming_complete': True}
    
    async def execute_solo_command(
        self,
        command: ParsedCommand,
        chat_id: int
    ) -> Dict[str, Any]:
        from session_manager import session_manager
        from telegram_handler import handle_solo
        
        content = command.content
        if not content:
            return {'success': False, 'error': 'No content provided for #solo'}
        
        await handle_solo(chat_id, content)
        
        return {'success': True, 'action': 'solo'}
    
    async def execute_stop_command(
        self,
        command: ParsedCommand,
        chat_id: int
    ) -> Dict[str, Any]:
        from telegram_handler import handle_stop
        
        await handle_stop(chat_id)
        
        return {'success': True, 'action': 'stopped'}
    
    async def execute_restart_command(
        self,
        command: ParsedCommand,
        update: Any,
        context: Any
    ) -> Dict[str, Any]:
        from telegram_handler import handle_restart
        
        status_msg = await update.message.reply_text("🔍 Checking for syntax errors before restart...")
        await handle_restart(update, context, status_msg)
        
        return {'success': True, 'action': 'restarting'}
    
    async def execute_assistant_command(
        self,
        command: ParsedCommand,
        update: Any,
        context: Any
    ) -> Dict[str, Any]:
        from llm_orchestrator import StreamOrchestrator
        from telegram.constants import ParseMode
        
        tag = command.assistant_tag
        prompt = command.content
        
        ast = manager.get_assistant(tag)
        if not ast:
            return {'success': False, 'error': f'Assistant #{tag} not found'}
        
        if not prompt:
            await update.message.reply_text(f"Please provide a prompt for #{tag}.")
            return {'success': False, 'error': 'No prompt provided'}
        
        status = await update.message.reply_text(f"🚀 Routing to {ast.name}...")
        _logger.info(f"Starting stream targeting exact model: {ast.get_model()}")
        
        stream_orchestrator = StreamOrchestrator(self.file_path, self.telegram_edit_rate_limit)
        await stream_orchestrator.run_streaming(prompt, update, context, status, assistant=ast)
        
        return {'success': True, 'action': 'assistant_stream_complete'}
    
    async def execute_brainstorm_command(
        self,
        command: ParsedCommand,
        update: Any,
        context: Any
    ) -> Dict[str, Any]:
        from llm_orchestrator import BRAINSTORM_SYSTEM
        from session_manager import session_manager
        from telegram_formatter import should_format
        from telegram_handler import _edit_with_retry
        from telegram_message_utils import split_message, split_message_with_code_block
        from telegram_formatter import format_for_telegram, get_parse_mode
        import asyncio
        import html
        
        chat_id = update.message.chat_id
        raw_text = command.content
        
        session_manager.add_message(chat_id, "user", raw_text, solo=False)
        
        window = session_manager.get_conversation_window(chat_id)
        
        streaming_msg = await update.message.reply_text("⏳ Thinking...")
        
        output_buffer = []
        token_count = 0
        
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
        import time
        last_edit_time = time.time()
        last_event_type = None
        last_tool_name = "tool"
        bubble_start_time = time.time()
        
        from assistants.base import StreamEventType
        
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
                        last_event_type = StreamEventType.REASONING
                        last_edit_time = time.time()
                        bubble_start_time = time.time()
                    
                    if event.content:
                        reasoning_buffer += event.content
                
                elif event.type == StreamEventType.TEXT:
                    if last_event_type != StreamEventType.TEXT and last_event_type is not None:
                        await update.message.reply_text("<b>✍️ Writing answer...</b>", parse_mode=ParseMode.HTML)
                        last_event_type = StreamEventType.TEXT
                        bubble_start_time = time.time()
                    
                    if event.content:
                        combined_output += event.content
                
                elif event.type == StreamEventType.TOOL_USE:
                    tool_name = event.metadata.get("name", "tool")
                    last_tool_name = tool_name
                    params = event.metadata.get("input", "")
                    
                    _logger.debug(f"Tool call: {tool_name}")
                    
                    await update.message.reply_text(
                        f"<b>🛠️ Calling:</b> <code>{html.escape(tool_name)}</code>",
                        parse_mode=ParseMode.HTML
                    )
                    last_event_type = StreamEventType.TOOL_USE
                    bubble_start_time = time.time()
                
                elif event.type == StreamEventType.TOOL_RESULT:
                    if event.content:
                        res_text = event.content
                        combined_output += f"\n[Tool Result]: {res_text}\n"
                        
                        await update.message.reply_text(
                            f"<b>📋 Result from:</b> <code>{html.escape(last_tool_name)}</code>\n\n<pre>{html.escape(res_text[:800])}</pre>",
                            parse_mode=ParseMode.HTML
                        )
                        last_event_type = StreamEventType.TOOL_RESULT
                        bubble_start_time = time.time()
        
        timer_task = None
        
        async def heartbeat():
            nonlocal last_edit_time
            while process.returncode is None:
                await asyncio.sleep(5)
                now = time.time()
                elapsed = int(now - bubble_start_time)
                if elapsed >= 10 and now - last_edit_time > 5:
                    last_edit_time = now
                    # We might want to edit a heartbeat message here if desired
        
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
            if timer_task:
                timer_task.cancel()
            raise e
        finally:
            if timer_task:
                timer_task.cancel()
        
        if process.returncode != 0:
            if assistant.is_rate_limit_error(error_output):
                if assistant.rotate_model():
                    _logger.warning(f"Retrying brainstorm with rotated model for {assistant.name}")
        
        response = combined_output.strip()
        
        try:
            chunks = split_message_with_code_block(response) if response else []
            
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
            _logger.error(f"Error sending message chunk: {e}")
        
        ast = manager.get_default_assistant()
        await update.message.reply_text(
            f"~ {ast.name} - {ast.get_model()}"
        )
        
        session_manager.add_message(chat_id, "assistant", response, solo=False)
        
        return {'success': True, 'action': 'brainstorm_complete', 'response': response}


_default_router: Optional[CommandRouter] = None


def get_command_router() -> CommandRouter:
    global _default_router
    if _default_router is None:
        _default_router = CommandRouter()
    return _default_router
