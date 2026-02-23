from __future__ import annotations
import asyncio
import contextlib
import html
from typing import Any, AsyncGenerator, Dict, Optional

from telegram.constants import ParseMode

from logger import get_logger

_logger = get_logger()

from core.events import (
    ContentDelta,
    DomainEvent,
    LifecycleEvent,
    LifecycleStatus,
    ProcessingFailed,
    ProgressUpdate,
    StateChanged,
    TaskInteraction,
    VisualIndicators,
    WorkflowState,
)
from core.interfaces import DeliveryInterface, ProgressPayload
from core.message import Message
from telegram_handler import _edit_with_retry
from telegram_message_utils import split_message
from telegram_formatter import format_for_telegram

class TelegramDeliveryAdapter(DeliveryInterface):
    def __init__(self, bot: Any) -> None:
        self.bot = bot

    async def send_message(
        self,
        message: Message,
        parse_mode: Optional[str] = ParseMode.HTML,
    ) -> Message:
        from telegram.error import TimedOut, RetryAfter, NetworkError
        
        async def _attempt_send():
            formatted_text = format_for_telegram(message.text)
            return await self.bot.send_message(
                chat_id=message.chat_id,
                text=formatted_text,
                parse_mode=parse_mode,
                reply_to_message_id=message.reply_to_id,
            )

        try:
            sent = await _attempt_send()
        except (TimedOut, RetryAfter, NetworkError) as e:
            _logger.warning(f"Send message failed (transient), retrying in 1s: {e}")
            await asyncio.sleep(1)
            try:
                sent = await _attempt_send()
            except Exception as e2:
                _logger.error(f"Send message failed after retry: {e2}")
                raise
        except Exception as e:
            _logger.error(f"Send message hard failure: {e}")
            raise

        return Message(
            user_id=sent.from_user.id if sent.from_user else None,
            chat_id=sent.chat_id,
            message_id=sent.message_id,
            text=sent.text or message.text,
            reply_to_id=sent.reply_to_message.message_id if sent.reply_to_message else None,
            metadata=message.metadata,
        )

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = ParseMode.HTML,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        await _edit_with_retry(
            self.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
        )
        return Message(user_id=None, chat_id=chat_id, message_id=message_id, text=text, metadata=metadata or {})

    async def update_progress_status(
        self,
        chat_id: int,
        message_id: int,
        payload: ProgressPayload,
    ) -> Message:
        timer = f" [Wait: {payload.elapsed}s]" if payload.elapsed is not None and payload.elapsed >= 1 else ""
        text_parts = [payload.header + timer]
        if payload.body:
            text_parts.append(f"\n\n<code>{html.escape(payload.body)}</code>")
        if payload.progress is not None:
            progress_text = f"Progress: {int(payload.progress * 100)}%"
            text_parts.append(progress_text)
        if payload.eta_seconds is not None:
            eta = payload.eta_seconds
            eta_text = f"ETA: {eta}s" if eta < 60 else f"ETA: {eta // 60}m{eta % 60}s"
            text_parts.append(eta_text)
        if payload.tokens is not None:
            text_parts.append(f"Tokens: {payload.tokens}")

        text = "\n".join(text_parts)
        await _edit_with_retry(
            self.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
        return Message(user_id=None, chat_id=chat_id, message_id=message_id, text=text)

    async def consume_domain_events(
        self,
        event_stream: AsyncGenerator[DomainEvent, None],
        request: Message,
    ) -> None:
        status_message_id: Optional[int] = None
        failure_reported = False
        reply_to_id = request.reply_to_id or request.message_id

        async for event in event_stream:
            _logger.debug(f"[DELIVERY EVENT] type={type(event).__name__} chat={request.chat_id}")

            if isinstance(event, LifecycleEvent):
                _logger.info(f"[DELIVERY LIFECYCLE] status={event.status.value} msg={event.message!r}")
                if event.status == LifecycleStatus.STARTED:
                    header = event.message or "🧠 Preparing your #code request..."
                    status = await self.send_message(
                        Message(None, request.chat_id, None, header, reply_to_id=request.message_id)
                    )
                    status_message_id = status.message_id
                    _logger.info(f"[DELIVERY] status bubble created: msg_id={status_message_id}")
                elif event.status == LifecycleStatus.COMPLETED and status_message_id is not None:
                    await self.edit_message(
                        request.chat_id,
                        status_message_id,
                        event.message or "✅ Workflow finished.",
                    )
                elif event.status == LifecycleStatus.FAILED:
                    _logger.error(f"[DELIVERY LIFECYCLE FAILED] chat={request.chat_id} msg={event.message!r}")
                    if status_message_id is not None:
                        with contextlib.suppress(Exception):
                            await self.edit_message(
                                request.chat_id,
                                status_message_id,
                                event.message or "⚠️ Workflow failed.",
                            )
                    if not failure_reported:
                        failure_text = event.message or "⚠️ Workflow failed."
                        with contextlib.suppress(Exception):
                            await self.send_message(
                                Message(None, request.chat_id, None, failure_text, reply_to_id=request.message_id)
                            )
                        failure_reported = True

            elif isinstance(event, ProcessingFailed):
                failure_reported = True
                stage_label = _state_banner(event.stage)
                error_text = f"⚠️ {stage_label}: {event.error}"
                _logger.error(f"[DELIVERY PROCESSING_FAILED] chat={request.chat_id} stage={event.stage} error={event.error!r}")
                if status_message_id is not None:
                    await self.edit_message(request.chat_id, status_message_id, error_text)
                await self.send_message(
                    Message(None, request.chat_id, None, error_text, reply_to_id=request.message_id)
                )

            elif isinstance(event, StateChanged) and status_message_id is not None:
                _logger.info(f"[DELIVERY STATE_CHANGED] chat={request.chat_id} state={event.state.value} details={event.details!r}")
                with contextlib.suppress(Exception):
                    await self.edit_message(
                        request.chat_id,
                        status_message_id,
                        _state_banner(event.state, event.details),
                    )

            elif isinstance(event, ProgressUpdate) and status_message_id is not None:
                with contextlib.suppress(Exception):
                    payload = _build_progress_payload(event)
                    _logger.debug(f"[DELIVERY PROGRESS] chat={request.chat_id} stage={event.stage.value} header={payload.header!r}")
                    await self.update_progress_status(request.chat_id, status_message_id, payload)

            elif isinstance(event, ContentDelta):
                _logger.info(f"[DELIVERY CONTENT_DELTA] chat={request.chat_id} length={len(event.text)} state={event.state.value}")
                _logger.debug(f"[DELIVERY CONTENT_DELTA TEXT] {event.text}")
                reply_to_id = await _send_content_chunks(event.text, request, self, reply_to_id)

            elif isinstance(event, TaskInteraction):
                _logger.info(f"[DELIVERY TASK_INTERACTION] chat={request.chat_id} question={event.question!r}")
                question_text = (
                    event.metadata.get("prompt") if event.metadata else event.question
                ) or event.question
                await self.send_message(
                    Message(
                        None,
                        request.chat_id,
                        None,
                        f'💬 The assistant asked: "{question_text}"\nReply with #prompt to continue.',
                        reply_to_id=request.message_id,
                    )
                )

            else:
                _logger.debug(f"[DELIVERY UNHANDLED EVENT] type={type(event).__name__} chat={request.chat_id} — no handler registered")



_STATE_LABELS: dict[WorkflowState, str] = {
    WorkflowState.TRANSCRIBING: "Transcribing conversation...",
    WorkflowState.THINKING: "Thinking through the plan...",
    WorkflowState.CODING: "Writing code...",
    WorkflowState.EXECUTING: "Executing final steps...",
}


def _state_banner(state: WorkflowState, details: Optional[str] = None) -> str:
    label = _STATE_LABELS.get(state, state.name.replace("_", " ").title())
    base = label
    if details:
        return f"{base}\n{details}"
    return base


def _visual_prefix(indicators: VisualIndicators) -> str:
    icons: list[str] = []
    if indicators.thinking:
        icons.append("🧠")
    if indicators.coding:
        icons.append("💻")
    if not icons:
        icons.append("⏳")
    return " ".join(icons)


def _build_progress_payload(event: ProgressUpdate) -> ProgressPayload:
    prefix = _visual_prefix(event.visual_indicators)
    header = f"{prefix} {_state_banner(event.stage)}".strip()
    lines: list[str] = []
    if event.message:
        lines.append(event.message)
    complexity_parts: list[str] = []
    if event.complexity_label:
        complexity_parts.append(event.complexity_label)
    if event.complexity_score is not None:
        complexity_parts.append(f"score={event.complexity_score:.3f}")
    if complexity_parts:
        lines.append("Complexity: " + " \u00b7 ".join(complexity_parts))
    if event.metadata:
        meta_items = [f"{key}: {value}" for key, value in event.metadata.items()]
        if meta_items:
            lines.append(" · ".join(meta_items))
    body = "\n".join(lines) if lines else None
    return ProgressPayload(
        header=header,
        body=body,
        elapsed=event.elapsed_s,
        tokens=event.tokens,
        progress=event.progress,
        eta_seconds=event.eta_seconds,
    )


async def _send_content_chunks(
    text: str,
    request: Message,
    delivery: TelegramDeliveryAdapter,
    reply_to_id: Optional[int],
) -> Optional[int]:
    next_reply = reply_to_id
    if not text.strip():
        return next_reply
    for chunk in split_message(text):
        if not chunk.strip():
            continue
        sent = await delivery.send_message(
            Message(None, request.chat_id, None, chunk, reply_to_id=next_reply)
        )
        if sent.message_id is not None:
            next_reply = sent.message_id
    return next_reply
