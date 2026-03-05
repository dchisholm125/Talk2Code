"""Telegram-focused prompt handler kept outside the core orchestrator."""

from __future__ import annotations

from typing import Iterable

from core.interfaces import DeliveryInterface, ProgressPayload
from core.message import Message
from llm_orchestrator import StreamOrchestrator
from session_manager import session_manager


async def handle_prompt_intent(
    request: Message,
    prompt: str,
    delivery: DeliveryInterface,
    file_path: str,
    edit_rate_limit: float = 0.5,
) -> None:
    chat_id = request.chat_id
    state = session_manager.get_or_create_session(chat_id)
    session_manager.add_message(chat_id, "user", prompt, solo=False)

    status_msg = await delivery.send_message(
        Message(None, chat_id, None, "🚀 Running prompt directly…", reply_to_id=request.message_id)
    )

    async def progress_callback(payload: ProgressPayload) -> None:
        if status_msg.message_id is None:
            return
        await delivery.update_progress_status(
            chat_id,
            status_msg.message_id,
            ProgressPayload(
                header="🚀 Running prompt directly…",
                body=payload.body,
                elapsed=payload.elapsed,
                tokens=payload.tokens,
                progress=payload.progress,
                eta_seconds=payload.eta_seconds,
            ),
        )

    streamer = StreamOrchestrator(file_path, edit_rate_limit)
    result = await streamer.run_streaming(
        prompt,
        status_msg,
        progress_callback=progress_callback,
    )

    if result.output:
        await _send_chunks(request, result.output, delivery)

    if result.question:
        session_manager.set_pending_question(state.session_id, result.question)
        await delivery.send_message(
            Message(
                None,
                chat_id,
                None,
                f"💬 The assistant is asking: \"{result.question}\"\nReply with #prompt to continue.",
                reply_to_id=request.message_id,
            )
        )

    session_manager.add_message(chat_id, "assistant", result.output or "", solo=False)


async def _send_chunks(
    request: Message,
    text: str,
    delivery: DeliveryInterface,
) -> None:
    if not text.strip():
        await delivery.send_message(
            Message(None, request.chat_id, None, "(empty response)", reply_to_id=request.message_id)
        )
        return

    reply_to = request.message_id
    for chunk in _chunk_text(text):
        sent = await delivery.send_message(
            Message(None, request.chat_id, None, chunk, reply_to_id=reply_to)
        )
        if sent.message_id:
            reply_to = sent.message_id


def _chunk_text(text: str, chunk_size: int = 3200) -> Iterable[str]:
    if not text:
        return []
    return (text[i : i + chunk_size] for i in range(0, len(text), chunk_size))
