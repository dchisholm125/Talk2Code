from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.events import SessionID

EVENT_LEDGER_PATH = Path.home() / ".voice-to-code" / "event-ledger.jsonl"


@dataclass
class TelemetryEvent:
    session_id: SessionID
    event_type: str
    timestamp: float
    payload: Dict[str, Any]
    reason: Optional[str] = None


class EventLedger:
    def __init__(self, path: Path = EVENT_LEDGER_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def log_event(
        self,
        session_id: SessionID,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        payload = payload or {}
        entry = {
            "session_id": int(session_id),
            "event_type": event_type,
            "timestamp": time.time(),
            "payload": payload,
            "reason": reason,
        }
        async with self._lock:
            await asyncio.to_thread(self._append_entry, entry)

    def _append_entry(self, entry: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as ledger:
            json.dump(entry, ledger)
            ledger.write("\n")

    def get_events(self, session_id: SessionID) -> List[TelemetryEvent]:
        if not self.path.exists():
            return []

        events: List[TelemetryEvent] = []
        with open(self.path, "r", encoding="utf-8") as ledger:
            for line in ledger:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if raw.get("session_id") != int(session_id):
                    continue
                events.append(TelemetryEvent(
                    session_id=SessionID(raw.get("session_id")),
                    event_type=raw.get("event_type", ""),
                    timestamp=raw.get("timestamp", 0.0),
                    payload=raw.get("payload", {}),
                    reason=raw.get("reason"),
                ))
        return events


_ledger: Optional[EventLedger] = None


def get_event_ledger() -> EventLedger:
    global _ledger
    if _ledger is None:
        _ledger = EventLedger()
    return _ledger
