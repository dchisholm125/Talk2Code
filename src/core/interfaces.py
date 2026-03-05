from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Optional, Protocol

from core.message import Message
from core.events import DomainEvent


@dataclass
class ProgressPayload:
    header: str
    body: Optional[str]
    elapsed: Optional[int] = None
    tokens: Optional[int] = None
    progress: Optional[float] = None
    eta_seconds: Optional[int] = None


class DeliveryInterface(Protocol):
    async def send_message(
        self,
        message: Message,
        parse_mode: Optional[str] = None,
    ) -> Message:
        ...  # pragma: no cover

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        ...  # pragma: no cover

    async def update_progress_status(
        self,
        chat_id: int,
        message_id: int,
        payload: ProgressPayload,
    ) -> Message:
        ...  # pragma: no cover

    async def consume_domain_events(
        self,
        event_stream: AsyncGenerator[DomainEvent, None],
        request: Message,
    ) -> None:
        ...  # pragma: no cover


@dataclass
class StreamingResult:
    output: str
    tokens: int
    question: Optional[str] = None
    assistant_name: str = ""
    model_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
