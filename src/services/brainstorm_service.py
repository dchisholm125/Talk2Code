from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncGenerator, Any

from core.events import (
    ContentDelta,
    DomainEvent,
    LifecycleEvent,
    LifecycleStatus,
    ProcessingFailed,
    SessionID,
    StateChanged,
    TaskInteraction,
    WorkflowState,
)
from core.interfaces import ProgressPayload
from core.message import Message
from llm_orchestrator import BRAINSTORM_SYSTEM, StreamOrchestrator
from observability.hub import get_observability_hub
from progress import ProgressTracker
from logger import get_logger
from session_manager import session_manager
from assistant_manager import manager
from telemetry import get_event_ledger

_logger = get_logger()


class BrainstormService:
    def __init__(self, file_path: str, edit_rate_limit: float = 0.5) -> None:
        self.file_path = file_path
        self.edit_rate_limit = edit_rate_limit
        self.streamer = StreamOrchestrator(file_path, edit_rate_limit)
        self.progress_tracker = ProgressTracker(event_sink=get_observability_hub().publish)
        self.event_ledger = get_event_ledger()

    async def stream_brainstorm(
        self,
        session_id: SessionID,
        chat_id: int,
        request: Message,
    ) -> AsyncGenerator[DomainEvent, None]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        async def producer() -> None:
            await self._produce_brainstorm(session_id, chat_id, request, queue, sentinel)

        task = asyncio.create_task(producer())
        try:
            while True:
                event = await queue.get()
                if event is sentinel:
                    break
                yield event  # type: ignore[arg-type]
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _produce_brainstorm(
        self,
        session_id: SessionID,
        chat_id: int,
        request: Message,
        queue: asyncio.Queue[Any],
        sentinel: object,
    ) -> None:
        current_state = WorkflowState.THINKING
        await self._emit(queue, LifecycleEvent(LifecycleStatus.STARTED, "Starting brainstorm"))
        await self._emit(queue, StateChanged(current_state, "Brainstorming with the assistant"))

        window = session_manager.get_conversation_window(chat_id)
        assistant = manager.get_default_assistant()
        extra = session_manager.format_session_history_for_prompt(session_id)

        prompt = assistant.format_prompt(window, BRAINSTORM_SYSTEM, extra_context=extra)
        placeholder = Message(None, session_id, None, "")
        await self.event_ledger.log_event(
            session_id,
            "LLM_Thought_Started",
            payload={"stage": "brainstorm", "reason": "Streaming brainstorming assistant"},
        )

        async def progress_sink(payload: ProgressPayload) -> None:
            event = await self.progress_tracker.emit_update(
                stage=current_state,
                payload=payload,
                metadata={"phase": "brainstorm"},
                session_id=session_id,
            )
            await self._emit(queue, event)

        try:
            result = await self.streamer.run_streaming(
                prompt,
                placeholder,
                agent="plan",
                progress_callback=progress_sink,
            )

            if result.output:
                await self._emit(queue, ContentDelta(text=result.output, state=current_state))
                session_manager.add_message(chat_id, "assistant", result.output, solo=False)

            if result.question:
                session_manager.set_pending_question(session_id, result.question)
                await self._emit(
                    queue,
                    TaskInteraction(
                        question=result.question,
                        stage=current_state,
                        metadata=result.metadata,
                    ),
                )

            await self._emit(queue, LifecycleEvent(LifecycleStatus.COMPLETED, "Brainstorm complete"))
        except Exception as exc:
            message = str(exc)
            _logger.log_exception("Brainstorm error", exc_info=exc)
            await self._emit(queue, ProcessingFailed(message, current_state, details=message))
            await self._emit(queue, LifecycleEvent(LifecycleStatus.FAILED, message))
        finally:
            await queue.put(sentinel)

    async def _emit(self, queue: asyncio.Queue[Any], event: DomainEvent) -> None:
        await queue.put(event)
