import asyncio
import subprocess
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from assistants.base import CodingAssistant, StreamEvent, StreamEventType
from assistant_manager import manager
from progress import ProgressTracker, ProcessingStage
from logger import get_logger
from progress_estimator import ProgressEstimator
from stages import HeartbeatManager, StageTracker
from core.interfaces import ProgressPayload, StreamingResult
from core.message import Message
from session_manager import session_manager

_logger = get_logger()

BRAINSTORM_SYSTEM = (
    "You are a collaborative thinking partner and software architect helping a developer "
    "brainstorm, plan, and refine their ideas. Engage thoughtfully, ask clarifying questions, "
    "and help sharpen concepts. You are NOT writing code right now — this is a shared thinking "
    "space. Keep responses concise and conversational. "
    "DO NOT use any tools. Just think and respond with your analysis. "
    "Reference the conversation above when relevant to maintain context across messages."
)

COMPRESS_SYSTEM = (
    "You are a technical writer specialising in software specifications. "
    "Given a brainstorming conversation between a developer and their AI assistant, "
    "synthesise it into a single, clear, and actionable implementation prompt for a coding "
    "assistant. Include all relevant technical details, constraints, and goals discussed. "
    "Write it as a direct, comprehensive instruction. No preamble."
)


class LLMOrchestrator:
    def __init__(self, file_path: str, edit_rate_limit: float = 0.5) -> None:
        self.file_path = file_path
        self.edit_rate_limit = edit_rate_limit

    async def run_assistant(
        self,
        prompt: str,
        assistant: Optional[CodingAssistant] = None,
        agent: str = "coder",
    ) -> str:
        if assistant is None:
            assistant = manager.get_default_assistant()

        _logger.log_stage_start(ProcessingStage.INVOKING_ASSISTANT, agent=agent, assistant=assistant.name)

        while True:
            cmd = assistant.get_command(prompt, agent=agent)
            start_time = time.time()

            try:
                _logger.debug(f"Executing command: {' '.join(cmd)}", duration_ms=0)

                result = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.file_path,
                )

                elapsed_ms = int((time.time() - start_time) * 1000)

                if result.returncode != 0:
                    if assistant.is_rate_limit_error(result.stderr):
                        if assistant.rotate_model():
                            _logger.warning(f"Rate limit hit, rotating model for {assistant.name}")
                            continue

                output = result.stdout
                if result.stderr:
                    output += f"\n\nSTDERR:\n{result.stderr}"
                output = self._strip_ansi(output)

                _logger.log_stage_complete(ProcessingStage.INVOKING_ASSISTANT, duration_ms=elapsed_ms)

                return output.strip() or f"Assistant {assistant.name} executed but returned no output."

            except subprocess.TimeoutExpired:
                _logger.log_stage_error(ProcessingStage.INVOKING_ASSISTANT, "timeout")
                return f"Error: {assistant.name} timed out after 300 seconds."
            except FileNotFoundError:
                _logger.log_stage_error(ProcessingStage.INVOKING_ASSISTANT, "command not found")
                return f"Error: Command '{cmd[0]}' not found — is it installed and on PATH?"
            except Exception as e:
                _logger.log_exception(f"Error in run_assistant: {e}")
                return f"Error: {e}"

    async def compress_conversation(
        self,
        window: List[Dict[str, Any]],
        status_message: Message,
        progress_callback: Optional[Callable[[ProgressPayload], Awaitable[None]]] = None,
        extra: str = "",
    ) -> str:
        _logger.log_stage_start(ProcessingStage.COMPRESSING)

        assistant = manager.get_default_assistant()
        convo_text = assistant.format_prompt(window, COMPRESS_SYSTEM, extra_context=extra)

        while True:
            cmd = assistant.get_command(convo_text, agent="plan", format_json=True)
            start_time = time.time()

            _logger.debug(f"Compressing conversation with command: {' '.join(cmd)}")

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.file_path,
                    limit=2**26,
                )

                output_buffer = ""
                reasoning_buffer = ""
                error_output = ""
                last_edit_time = time.time()
                last_activity = time.time()
                bubble_start_time = time.time()
                active_header = "Compressing conversation..."

                async def emit_progress(body: str, elapsed: float) -> None:
                    nonlocal last_edit_time
                    if not progress_callback:
                        return
                    now = time.time()
                    if now - last_edit_time < self.edit_rate_limit:
                        return
                    last_edit_time = now
                    payload = ProgressPayload(
                        header=active_header,
                        body=body[-3500:],
                        elapsed=int(elapsed),
                    )
                    await progress_callback(payload)

                async def read_stream_with_timeout(stream, is_stderr=False):
                    nonlocal output_buffer, reasoning_buffer, error_output, last_activity, last_edit_time

                    while True:
                        if session_manager.is_cancelled(status_message.chat_id):
                            raise asyncio.CancelledError("User requested stop.")

                        try:
                            line = await asyncio.wait_for(stream.readline(), timeout=5.0)
                        except asyncio.TimeoutError:
                            if time.time() - last_activity > 120.0:
                                raise TimeoutError("Assistant process hung for too long.")
                            continue

                        if not line:
                            break

                        last_activity = time.time()
                        line_decoded = line.decode("utf-8").strip()

                        if is_stderr:
                            error_output += line_decoded + "\n"
                            if line_decoded:
                                _logger.warning(f"[OPENCODE COMPRESS STDERR] {line_decoded}")
                            continue

                        if line_decoded:
                            _logger.debug(f"[OPENCODE COMPRESS RAW] {line_decoded}")

                        event = assistant.parse_line(line_decoded)
                        if not event:
                            continue

                        if event.type == StreamEventType.REASONING:
                            if event.content:
                                reasoning_buffer += event.content
                                _logger.debug(f"[OPENCODE COMPRESS THINKING] {event.content}")
                            now = time.time()
                            if now - last_edit_time > self.edit_rate_limit:
                                last_edit_time = now
                                await emit_progress(reasoning_buffer, now - bubble_start_time)
                        elif event.type == StreamEventType.TEXT:
                            if event.content:
                                output_buffer += event.content
                                _logger.debug(f"[OPENCODE COMPRESS OUTPUT] {event.content}")
                            now = time.time()
                            if now - last_edit_time > self.edit_rate_limit:
                                last_edit_time = now
                                await emit_progress(output_buffer, now - bubble_start_time)

                async def heartbeat():
                    progress_estimator = ProgressEstimator()
                    progress_estimator.set_current_stage(ProcessingStage.COMPRESSING)
                    heartbeat_manager = HeartbeatManager(interval_seconds=8)

                    def log_to_backend():
                        return {"eta_seconds": progress_estimator.get_progress().get("eta_seconds")}

                    heartbeat_manager.add_callback(log_to_backend)
                    stage_tracker = StageTracker()
                    stage_tracker.start_stage(ProcessingStage.COMPRESSING)
                    hb_task = asyncio.create_task(heartbeat_manager.start(stage_tracker))

                    try:
                        while process.returncode is None:
                            await asyncio.sleep(5)
                            now = time.time()
                            elapsed = now - bubble_start_time
                            progress_data = progress_estimator.get_progress()
                            eta_seconds = progress_data.get("eta_seconds")
                            progress_estimator.update_tokens(len(output_buffer))
                            await emit_progress(output_buffer, elapsed)
                    finally:
                        hb_task.cancel()
                        await heartbeat_manager.stop()

                timer_task = asyncio.create_task(heartbeat())
                try:
                    await asyncio.gather(
                        read_stream_with_timeout(process.stdout, is_stderr=False),
                        read_stream_with_timeout(process.stderr, is_stderr=True)
                    )
                    await process.wait()
                except (asyncio.CancelledError, Exception) as e:
                    process.terminate()
                    session_manager.unmark_cancelled(status_message.chat_id)
                    if isinstance(e, asyncio.CancelledError):
                        _logger.info(f"Compression cancelled for chat {status_message.chat_id}")
                    raise
                finally:
                    timer_task.cancel()

                try:
                    await asyncio.wait_for(process.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    process.terminate()

                elapsed_ms = int((time.time() - start_time) * 1000)

                if process.returncode != 0:
                    if assistant.is_rate_limit_error(error_output):
                        if assistant.rotate_model():
                            _logger.warning("Retrying compress with rotated model")
                            continue

                _logger.log_stage_complete(ProcessingStage.COMPRESSING, duration_ms=elapsed_ms, output_length=len(output_buffer))
                return output_buffer.strip()

            except Exception as e:
                _logger.log_stage_error(ProcessingStage.COMPRESSING, str(e))
                raise

    def _strip_ansi(self, text: str) -> str:
        import re

        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub("", text)


class StreamOrchestrator:
    def __init__(self, file_path: str, edit_rate_limit: float = 0.5) -> None:
        self.file_path = file_path
        self.edit_rate_limit = edit_rate_limit

    async def run_streaming(
        self,
        prompt: str,
        status_message: Message,
        assistant: Optional[CodingAssistant] = None,
        agent: str = "coder",
        progress_callback: Optional[Callable[[ProgressPayload], Awaitable[None]]] = None,
        on_progress: Optional[Callable[[ProgressTracker], None]] = None,
        event_sink: Optional[Callable[[StreamEvent], Awaitable[None]]] = None,
        _continuation_count: int = 0,
    ) -> StreamingResult:
        if assistant is None:
            assistant = manager.get_default_assistant()

        chat_id = status_message.chat_id
        if session_manager.is_cancelled(chat_id):
            session_manager.unmark_cancelled(chat_id)

        _logger.log_stage_start(ProcessingStage.INVOKING_ASSISTANT, agent=agent, assistant=assistant.name)

        while True:
            cmd = assistant.get_command(prompt, agent=agent, format_json=True)
            start_time = time.time()

            # ── Developer visibility: full command + prompt excerpt ─────────────
            _logger.info(f"[OPENCODE SPAWN] cmd={' '.join(cmd[:5])} ... ({len(cmd)} args)")
            _logger.debug(f"[OPENCODE SPAWN FULL CMD] {' '.join(cmd)}")
            _logger.info(f"[OPENCODE PROMPT EXCERPT] {prompt[:500]}{'...' if len(prompt)>500 else ''}")

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.file_path,
                    limit=1024 * 1024,
                )

                output_buffer = ""
                error_output = ""
                last_activity = time.time()
                last_edit_time = time.time()
                bubble_start_time = time.time()
                token_count = 0
                active_header = "Assistant starting..."
                active_body = ""
                last_event_type = None
                last_tool_name = "tool"

                progress = ProgressTracker()
                progress.start_stage(ProcessingStage.INVOKING_ASSISTANT, "Starting assistant...")

                async def emit_progress() -> None:
                    nonlocal last_edit_time
                    if not progress_callback:
                        return
                    now = time.time()
                    if now - last_edit_time < self.edit_rate_limit:
                        return
                    last_edit_time = now
                    elapsed = int(now - bubble_start_time)
                    payload = ProgressPayload(
                        header=active_header,
                        body=active_body[-3500:] if active_body else output_buffer[-3500:],
                        elapsed=elapsed,
                        tokens=token_count,
                    )
                    await progress_callback(payload)

                async def read_stream_with_timeout(stream, is_stderr=False):
                    nonlocal output_buffer, error_output, last_activity, active_header, active_body, last_event_type, bubble_start_time, last_tool_name
                    nonlocal token_count, last_edit_time

                    while True:
                        if session_manager.is_cancelled(chat_id):
                            raise asyncio.CancelledError("User requested stop.")

                        try:
                            line = await asyncio.wait_for(stream.readline(), timeout=5.0)
                        except asyncio.TimeoutError:
                            if time.time() - last_activity > 120.0:
                                raise TimeoutError("Assistant process hung for too long.")
                            continue

                        if not line:
                            break

                        last_activity = time.time()

                        line_decoded = line.decode("utf-8").strip()
                        if is_stderr:
                            error_output += line_decoded + "\n"
                            # Tee stderr immediately so developers never miss subprocess errors
                            if line_decoded:
                                _logger.warning(f"[OPENCODE STDERR] {line_decoded}")
                            continue

                        # ── Log every raw JSON line at debug level (full transparency) ─────
                        if line_decoded:
                            _logger.debug(f"[OPENCODE RAW] {line_decoded}")

                        event = assistant.parse_line(line_decoded)
                        if not event:
                            continue

                        if event_sink:
                            await event_sink(event)

                        if event.type == StreamEventType.REASONING:
                            token_count += 1
                            progress.start_stage(ProcessingStage.THINKING, "Thinking...")

                            if last_event_type != StreamEventType.REASONING:
                                active_header = "🤔 Thinking..."
                                active_body = ""
                                bubble_start_time = time.time()
                                last_event_type = StreamEventType.REASONING
                                _logger.info(f"[OPENCODE THINKING START] agent={agent} elapsed={int(time.time()-bubble_start_time)}s")

                            if event.content:
                                active_body += event.content
                                output_buffer += event.content
                                # Log every reasoning chunk in full — this is the stream of thought
                                _logger.debug(f"[OPENCODE THINKING] {event.content}")

                            if on_progress:
                                on_progress(progress)

                            await emit_progress()

                        elif event.type == StreamEventType.TEXT:
                            if last_event_type != StreamEventType.TEXT and last_event_type is not None:
                                active_header = "✍️ Writing answer..."
                                active_body = ""
                                bubble_start_time = time.time()
                                progress.start_stage(ProcessingStage.WRITING, "Writing response...")
                                _logger.info(f"[OPENCODE OUTPUT START] agent={agent}")
                            if event.content:
                                active_body += event.content
                                output_buffer += event.content
                                # Log every text chunk in full so the developer sees what's being written
                                _logger.debug(f"[OPENCODE OUTPUT] {event.content}")
                            if on_progress:
                                on_progress(progress)
                            await emit_progress()

                        elif event.type == StreamEventType.TOOL_USE:
                            name = event.metadata.get("name", "tool")
                            last_tool_name = name
                            inp = event.metadata.get("input", "")

                            # Full tool call visibility — name AND complete input
                            _logger.info(f"[OPENCODE TOOL_USE] name={name}")
                            if inp:
                                _logger.info(f"[OPENCODE TOOL_USE INPUT]\n{inp}")
                            progress.start_stage(ProcessingStage.TOOL_EXECUTION, f"Calling {name}...")

                            active_header = f"🛠️ Calling: {name}"
                            active_body = f"Params: {inp}" if inp else ""
                            bubble_start_time = time.time()
                            last_event_type = StreamEventType.TOOL_USE
                            await emit_progress()

                            if on_progress:
                                on_progress(progress)

                        elif event.type == StreamEventType.TOOL_RESULT:
                            if event.content:
                                res_text = event.content
                                output_buffer += res_text
                                active_header = f"📋 Result from: {last_tool_name}"
                                active_body = res_text[:800]
                                bubble_start_time = time.time()
                                # Full tool result — developers need to see what the tool returned
                                _logger.info(f"[OPENCODE TOOL_RESULT] from={last_tool_name}")
                                _logger.debug(f"[OPENCODE TOOL_RESULT CONTENT]\n{res_text}")
                            last_event_type = StreamEventType.TOOL_RESULT
                            last_activity = time.time()
                            await emit_progress()

                async def heartbeat():
                    nonlocal active_header, active_body
                    progress_estimator = ProgressEstimator()
                    progress_estimator.set_current_stage(ProcessingStage.INVOKING_ASSISTANT)
                    heartbeat_manager = HeartbeatManager(interval_seconds=8)

                    def log_to_backend():
                        return {"eta_seconds": progress_estimator.get_progress().get("eta_seconds")}

                    heartbeat_manager.add_callback(log_to_backend)
                    stage_tracker = StageTracker()
                    stage_tracker.start_stage(ProcessingStage.INVOKING_ASSISTANT)
                    hb_task = asyncio.create_task(heartbeat_manager.start(stage_tracker))

                    try:
                        while process.returncode is None:
                            await asyncio.sleep(5)
                            now = time.time()
                            elapsed = int(now - bubble_start_time)
                            progress_data = progress_estimator.get_progress()
                            eta_seconds = progress_data.get("eta_seconds")
                            progress_estimator.update_tokens(token_count)
                            payload = ProgressPayload(
                                header=active_header,
                                body=active_body[-3500:] if active_body else output_buffer[-3500:],
                                elapsed=elapsed,
                                tokens=token_count,
                                progress=progress_data.get("progress"),
                                eta_seconds=eta_seconds,
                            )
                            if progress_callback:
                                await progress_callback(payload)
                    finally:
                        hb_task.cancel()
                        await heartbeat_manager.stop()

                timer_task = asyncio.create_task(heartbeat())
                try:
                    await asyncio.gather(
                        read_stream_with_timeout(process.stdout, is_stderr=False),
                        read_stream_with_timeout(process.stderr, is_stderr=True),
                    )
                    await process.wait()
                except asyncio.CancelledError:
                    _logger.info(f"Streaming for chat {chat_id} was cancelled.")
                    process.terminate()
                    session_manager.unmark_cancelled(chat_id)
                    raise
                except Exception:
                    process.terminate()
                    session_manager.unmark_cancelled(chat_id)
                    raise
                finally:
                    timer_task.cancel()

                try:
                    await asyncio.wait_for(process.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    process.terminate()
                    raise TimeoutError("Assistant process failed to exit cleanly.")

                elapsed_ms = int((time.time() - start_time) * 1000)

                if process.returncode != 0:
                    if assistant.is_rate_limit_error(error_output):
                        if assistant.rotate_model():
                            _logger.warning("Retrying stream with rotated model")
                            continue

                _logger.log_stage_complete(
                    ProcessingStage.INVOKING_ASSISTANT,
                    duration_ms=elapsed_ms,
                    tokens=token_count,
                    output_length=len(output_buffer),
                )

                # ── Detect empty output (usually indicates model/API error) ─────────────
                if token_count == 0 and output_buffer.strip() == "":
                    error_msg = f"Assistant returned no output. stderr: {error_output[:500] if error_output else 'none'}"
                    _logger.error(f"[OPENCODE ERROR] {error_msg}")
                    raise RuntimeError(f"Assistant failed: no output generated. Model may be unavailable or invalid.")

                # ── Full exit summary ────────────────────────────────────────────
                _logger.info(
                    f"[OPENCODE EXIT] returncode={process.returncode} "
                    f"elapsed={elapsed_ms}ms tokens={token_count} output_bytes={len(output_buffer)}"
                )
                if error_output.strip():
                    _logger.warning(f"[OPENCODE STDERR SUMMARY]\n{error_output.strip()}")

                clean_output = self._strip_ansi(output_buffer.strip())
                question = self._detect_question(clean_output)
                metadata: Dict[str, Any] = {}

                if question:
                    state = session_manager.get_or_create_session(chat_id)
                    session_manager.set_pending_question(state.session_id, question)
                    metadata["question"] = question

                model_provider = getattr(assistant, "get_model", lambda: "")
                return StreamingResult(
                    output=clean_output,
                    tokens=token_count,
                    question=question,
                    assistant_name=assistant.name,
                    model_name=model_provider(),
                    metadata=metadata,
                )

            except FileNotFoundError:
                _logger.error(
                    f"[OPENCODE ERROR] CLI command not found: '{cmd[0]}'. "
                    "Is opencode installed and on PATH? Run: which opencode"
                )
                raise
            except asyncio.CancelledError:
                _logger.info(f"[OPENCODE CANCELLED] agent={agent} chat_id={chat_id}")
                raise
            except TimeoutError as e:
                _logger.error(f"[OPENCODE TIMEOUT] agent={agent} chat_id={chat_id}: {e}")
                raise
            except Exception as e:
                _logger.log_exception(f"[OPENCODE EXCEPTION] agent={agent} chat_id={chat_id}: {e}")
                raise

    def _strip_ansi(self, text: str) -> str:
        import re

        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub("", text)

    def _detect_question(self, text: str) -> Optional[str]:
        lines = text.strip().split("\n")
        last_lines = lines[-5:] if len(lines) >= 5 else lines
        question_lines = [l.strip() for l in last_lines if "?" in l and len(l.strip()) > 10]
        if question_lines:
            return question_lines[-1]
        return None
