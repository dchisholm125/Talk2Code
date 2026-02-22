"""Event-driven orchestrator that manages transcription → planning → execution."""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncGenerator, Dict, Optional, Any, cast

from assistants.base import StreamEvent, StreamEventType
from core.events import (
    ContentDelta,
    DomainEvent,
    LifecycleEvent,
    LifecycleStatus,
    ProcessingFailed,
    ProgressUpdate,
    SessionID,
    StateChanged,
    TaskInteraction,
    WorkflowState,
)
from core.interfaces import ProgressPayload, StreamingResult
from observability.hub import get_observability_hub
from progress import ProgressTracker
from llm_orchestrator import LLMOrchestrator, StreamOrchestrator
from logger import get_logger
from progress_estimator import ProgressEstimator
from session_manager import session_manager
from core.message import Message
from telemetry import get_event_ledger

_logger = get_logger()

# Improvement #2: injected into every coding prompt so the assistant re-reads
# files after each edit and verifies syntax before finishing.
_CODING_VERIFICATION_SUFFIX = (
    "\n\n---\n"
    "**Verification requirement (mandatory):**\n"
    "• After every file modification, immediately re-read the changed file to "
    "confirm the patch applied exactly as intended before proceeding.\n"
    "• Before finishing the task, run `python -m py_compile <file>` on each "
    "Python file you modified and fix any syntax errors that are reported."
)


class OrchestratorService:
    def __init__(self, file_path: str, edit_rate_limit: float = 0.5) -> None:
        self.file_path = file_path
        self.edit_rate_limit = edit_rate_limit
        self.llm = LLMOrchestrator(file_path, edit_rate_limit)
        self.streamer = StreamOrchestrator(file_path, edit_rate_limit)
        self.progress_tracker = ProgressTracker(event_sink=get_observability_hub().publish)
        self.event_ledger = get_event_ledger()

    async def stream_code_workflow(
        self,
        session_id: SessionID,
        chat_id: int,
        user_text: str,
        extra: str = "",
    ) -> AsyncGenerator[DomainEvent, None]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()

        async def producer() -> None:
            await self._produce_code_events(session_id, chat_id, user_text, extra, queue, sentinel)

        task = asyncio.create_task(producer())
        try:
            while True:
                event = await queue.get()
                if event is sentinel:
                    break
                yield cast(DomainEvent, event)
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _produce_code_events(
        self,
        session_id: SessionID,
        chat_id: int,
        user_text: str,
        extra: str,
        queue: asyncio.Queue[object],
        sentinel: object,
    ) -> None:
        current_state = WorkflowState.TRANSCRIBING
        try:
            await self._emit(queue, LifecycleEvent(LifecycleStatus.STARTED, "Processing #code request"), session_id)
            await self._emit(queue, StateChanged(current_state, "Capturing conversation window"), session_id)

            window = session_manager.get_conversation_window(chat_id)
            if not window:
                message = "💭 Nothing to compress yet — send some context before #code."
                await self._emit(queue, ProcessingFailed(message, current_state), session_id)
                await self._emit(queue, LifecycleEvent(LifecycleStatus.FAILED, message), session_id)
                return

            current_state = WorkflowState.THINKING
            await self._emit(queue, StateChanged(current_state, "Compressing conversation into a prompt"), session_id)
            await self._log_llm_thought(session_id, "compressing", "Compressing conversation into a prompt")

            prompt = await self._compress_conversation(session_id, window, extra, queue)
            # Improvement #2: instruct the coding agent to verify its own edits
            prompt += _CODING_VERIFICATION_SUFFIX
            session_manager.advance_window(chat_id)
            session_manager.add_message(chat_id, "user", user_text, solo=False)

            estimator = ProgressEstimator()
            complexity = estimator.analyze_prompt_complexity(prompt)
            complexity_label = complexity.get("complexity_label")
            complexity_score = complexity.get("complexity_score")
            eta_seconds = int(complexity.get("estimated_duration", 0))
            complexity_message = (
                f"Prompt complexity: {complexity_label}" if complexity_label else "Prompt complexity evaluated."
            )
            complexity_event = await self.progress_tracker.emit_update(
                stage=current_state,
                message=complexity_message,
                metadata={"phase": "complexity"},
                complexity_label=complexity_label,
                complexity_score=complexity_score,
                eta_seconds=eta_seconds,
                session_id=session_id,
            )
            await self._emit(queue, complexity_event, session_id)
            _logger.info(
                f"Prompt complexity: {complexity_label} (score={float(complexity_score or 0.0):.3f}) "
                f"ETA={eta_seconds}s"
            )

            current_state = WorkflowState.CODING
            await self._emit(queue, StateChanged(current_state, "Running the assistant"), session_id)
            await self._log_llm_thought(session_id, "coding", "Invoking the coding assistant")

            result = await self._execute_streaming(prompt, session_id, queue, current_state)
            session_manager.add_message(chat_id, "assistant", result.output or "", solo=False)

            if result.question:
                session_manager.set_pending_question(session_id, result.question)
                await self._emit(
                    queue,
                    TaskInteraction(
                        question=result.question,
                        stage=current_state,
                        metadata=result.metadata,
                    ),
                    session_id,
                )

            current_state = WorkflowState.EXECUTING
            await self._emit(queue, StateChanged(current_state, "Execution complete"), session_id)
            await self._emit(queue, LifecycleEvent(LifecycleStatus.COMPLETED, "Workflow finished"), session_id)
        except asyncio.CancelledError as exc:
            message = "⛔ Workflow cancelled"
            await self._emit(queue, ProcessingFailed(str(exc), current_state, details=message), session_id)
            await self._emit(queue, LifecycleEvent(LifecycleStatus.FAILED, message), session_id)
        except Exception as exc:  # pragma: no cover - best effort
            message = str(exc)
            await self._emit(queue, ProcessingFailed(message, current_state), session_id)
            await self._emit(queue, LifecycleEvent(LifecycleStatus.FAILED, message), session_id)
        finally:
            await queue.put(sentinel)

    async def _compress_conversation(
        self,
        session_id: SessionID,
        window: list[Dict[str, Any]],
        extra: str,
        queue: asyncio.Queue[object],
    ) -> str:
        async def progress_sink(payload: ProgressPayload) -> None:
            event = await self.progress_tracker.emit_update(
                stage=WorkflowState.THINKING,
                payload=payload,
                metadata={"phase": "compressing"},
                session_id=session_id,
            )
            await self._emit(queue, event, session_id)

        placeholder = Message(None, session_id, None, "")
        prompt = await self.llm.compress_conversation(
            window,
            placeholder,
            progress_callback=progress_sink,
            extra=extra,
        )
        return prompt

    async def _execute_streaming(
        self,
        prompt: str,
        session_id: SessionID,
        queue: asyncio.Queue[object],
        initial_stage: WorkflowState,
    ) -> StreamingResult:
        current_stage = initial_stage
        had_content = False

        async def progress_sink(payload: ProgressPayload) -> None:
            event = await self.progress_tracker.emit_update(
                stage=current_stage,
                payload=payload,
                session_id=session_id,
            )
            await self._emit(queue, event, session_id)

        async def event_sink(event: StreamEvent) -> None:
            nonlocal current_stage, had_content

            if event.type == StreamEventType.TOOL_USE:
                tool_name = (event.metadata or {}).get("name", "tool")
                await self.event_ledger.log_event(
                    session_id,
                    "ToolExecution",
                    payload={"tool": tool_name, "metadata": event.metadata or {}},
                )
                if current_stage != WorkflowState.EXECUTING:
                    current_stage = WorkflowState.EXECUTING
                    await self._emit(queue, StateChanged(current_stage, "Executing tool"), session_id)
            elif event.type in {StreamEventType.TEXT, StreamEventType.REASONING}:
                if current_stage != WorkflowState.CODING:
                    current_stage = WorkflowState.CODING
                    await self._emit(queue, StateChanged(current_stage, "Resuming code generation"), session_id)

            if event.content:
                had_content = True
                await self._emit(queue, ContentDelta(text=event.content, state=current_stage), session_id)

            if event.type == StreamEventType.TOOL_RESULT and current_stage != WorkflowState.CODING:
                current_stage = WorkflowState.CODING
                await self._emit(queue, StateChanged(current_stage, "Tool finished"), session_id)

        placeholder = Message(None, session_id, None, "")
        result = await self.streamer.run_streaming(
            prompt,
            placeholder,
            progress_callback=progress_sink,
            event_sink=event_sink,
        )

        if not had_content and result.output:
            await self._emit(queue, ContentDelta(text=result.output, state=current_stage), session_id)

        return result

    async def _log_llm_thought(self, session_id: SessionID, stage: str, reason: str) -> None:
        await self.event_ledger.log_event(
            session_id,
            "LLM_Thought_Started",
            payload={"stage": stage, "reason": reason},
        )

    async def _emit(
        self,
        queue: asyncio.Queue[object],
        event: DomainEvent,
        session_id: SessionID,
    ) -> None:
        if isinstance(event, StateChanged):
            await self.event_ledger.log_event(
                session_id,
                "StateUpdate",
                payload={"state": event.state.value, "details": event.details or ""},
            )
        await queue.put(event)
