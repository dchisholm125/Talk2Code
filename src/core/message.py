from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Message:
    user_id: Optional[int]
    chat_id: int
    message_id: Optional[int]
    text: str
    reply_to_id: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "text": self.text,
            "reply_to_id": self.reply_to_id,
            **self.metadata,
        }
