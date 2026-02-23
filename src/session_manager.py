from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.events import ContextEnvelope, SessionID
from logger import get_logger
from telemetry import get_event_ledger

SESSION_STATE_PATH = Path.home() / ".voice-to-code" / "sessions-state.json"

_logger = get_logger()


@dataclass
class SessionState:
    session_id: SessionID
    chat_id: int
    created_at: str
    last_active: str
    window_start: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    context_envelope: Dict[str, Any] = field(default_factory=dict)
    working_set: List[str] = field(default_factory=list)
    pending_question: Optional[str] = None
    cancelled: bool = False
    consecutive_empty_responses: int = 0

    def touch(self) -> None:
        self.last_active = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": int(self.session_id),
            "chat_id": self.chat_id,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "window_start": self.window_start,
            "history": self.history,
            "context_envelope": self.context_envelope,
            "working_set": self.working_set,
            "pending_question": self.pending_question,
            "cancelled": self.cancelled,
            "consecutive_empty_responses": self.consecutive_empty_responses,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        return cls(
            session_id=SessionID(int(data.get("session_id", 0))),
            chat_id=int(data.get("chat_id", 0)),
            created_at=data.get("created_at", datetime.utcnow().isoformat()),
            last_active=data.get("last_active", datetime.utcnow().isoformat()),
            window_start=int(data.get("window_start", 0)),
            history=data.get("history", []),
            context_envelope=data.get("context_envelope", {}),
            working_set=data.get("working_set", []),
            pending_question=data.get("pending_question"),
            cancelled=data.get("cancelled", False),
            consecutive_empty_responses=data.get("consecutive_empty_responses", 0),
        )


class SessionManager:
    def __init__(self) -> None:
        self.event_ledger = get_event_ledger()
        self.state_path = SESSION_STATE_PATH
        self.sessions: Dict[SessionID, SessionState] = self._load_session_states()
        self.chat_index: Dict[int, SessionID] = {
            state.chat_id: state.session_id for state in self.sessions.values()
        }
        self._next_session_id = max((int(sid) for sid in self.sessions.keys()), default=0) + 1
        for sid in list(self.sessions.keys()):
            self._rehydrate_session(sid)
        self.pending_model_selections: Dict[int, str] = {}

    # ── Session state persistence ────────────────────────────────────────────

    def _load_session_states(self) -> Dict[SessionID, SessionState]:
        if not self.state_path.exists():
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as fp:
                raw = json.load(fp)
        except (json.JSONDecodeError, IOError):
            _logger.warning("Failed to load session states")
            return {}
        result: Dict[SessionID, SessionState] = {}
        for sid_str, payload in (raw or {}).items():
            try:
                sid = SessionID(int(sid_str))
            except ValueError:
                continue
            result[sid] = SessionState.from_dict(payload)
        return result

    def _save_session_states(self) -> None:
        payload = {str(sid): state.to_dict() for sid, state in self.sessions.items()}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)

    def _persist_state(self, state: SessionState) -> None:
        self.sessions[state.session_id] = state
        self.chat_index[state.chat_id] = state.session_id
        self._save_session_states()

    def _create_session(self, chat_id: int) -> SessionState:
        session = SessionState(
            session_id=SessionID(self._next_session_id),
            chat_id=chat_id,
            created_at=datetime.utcnow().isoformat(),
            last_active=datetime.utcnow().isoformat(),
        )
        self._next_session_id += 1
        self._persist_state(session)
        return session

    def _resolve_session(self, identifier: int) -> SessionState:
        sid = SessionID(identifier)
        if sid in self.sessions:
            return self.sessions[sid]
        if identifier in self.chat_index:
            mapped = self.chat_index[identifier]
            return self.sessions[mapped]
        return self._create_session(identifier)

    def _rehydrate_session(self, session_id: SessionID) -> None:
        state = self.sessions.get(session_id)
        if not state:
            return
        events = self.event_ledger.get_events(session_id)
        for event in events:
            if event.event_type != "ContextSnapshotTaken":
                continue
            envelope = event.payload.get("envelope")
            if isinstance(envelope, dict):
                state.context_envelope = envelope
                state.working_set = envelope.get("working_set", [])
        self._persist_state(state)

    # ── Conversation helpers ────────────────────────────────────────────────

    def get_or_create_session(self, chat_id: int) -> SessionState:
        if chat_id in self.chat_index:
            sid = self.chat_index[chat_id]
            return self.sessions[sid]
        return self._create_session(chat_id)

    def add_message(self, chat_id: int, role: str, content: str, solo: bool = False) -> None:
        state = self.get_or_create_session(chat_id)
        last_entry = state.history[-1] if state.history else None
        if last_entry and last_entry.get("role") == role and last_entry.get("content") == content and last_entry.get("solo") == solo:
            _logger.debug(
                f"Skipping duplicate message for chat {chat_id}: role={role} content={content[:40]}"
            )
            return
        state.history.append({"role": role, "content": content, "solo": solo})
        state.touch()
        self._persist_state(state)

    def get_conversation_window(self, chat_id: int) -> List[Dict[str, Any]]:
        state = self.get_or_create_session(chat_id)
        return state.history[state.window_start :]

    def advance_window(self, chat_id: int) -> None:
        state = self.get_or_create_session(chat_id)
        state.window_start = len(state.history)
        state.touch()
        self._persist_state(state)

    def clear_conversation(self, chat_id: int) -> None:
        state = self.get_or_create_session(chat_id)
        state.history.clear()
        state.window_start = 0
        state.context_envelope.clear()
        state.working_set.clear()
        state.pending_question = None
        state.cancelled = False
        state.touch()
        self._persist_state(state)

    # ── Cancellation hooks ─────────────────────────────────────────────────

    def cancel_session(self, chat_id: int) -> None:
        state = self.get_or_create_session(chat_id)
        state.cancelled = True
        state.touch()
        self._persist_state(state)

    def unmark_cancelled(self, chat_id: int) -> None:
        state = self.get_or_create_session(chat_id)
        state.cancelled = False
        self._persist_state(state)

    def is_cancelled(self, chat_id: int) -> bool:
        state = self.get_or_create_session(chat_id)
        return state.cancelled

    # ── Session history / narrative ─────────────────────────────────────────

    def format_current_context_for_prompt(
        self, session_id: Optional[SessionID] = None
    ) -> str:
        lines = []
        if session_id:
            state = self.sessions.get(session_id)
            summary_text = state.context_envelope.get("summary_text") if state else ""
            if summary_text:
                lines.append("\nContext envelope:\n" + summary_text)
        return "\n".join(lines)

    # ── Pending question helpers ───────────────────────────────────────────

    def set_pending_question(self, session_id: SessionID, question: str) -> None:
        state = self._resolve_session(int(session_id))
        state.pending_question = question
        state.touch()
        self._persist_state(state)

    def get_pending_question(self, chat_id: int) -> Optional[str]:
        state = self.get_or_create_session(chat_id)
        return state.pending_question

    def clear_pending_question(self, chat_id: int) -> None:
        state = self.get_or_create_session(chat_id)
        state.pending_question = None
        self._persist_state(state)

    def context_summary_for_prompt(self, session_id: SessionID) -> str:
        state = self.sessions.get(session_id)
        if not state:
            return ""
        return state.context_envelope.get("summary_text", "")

    def update_context_envelope(self, session_id: SessionID, envelope: ContextEnvelope) -> None:
        state = self.sessions.get(session_id)
        if not state:
            return
        context_dict = asdict(envelope)
        state.context_envelope = context_dict
        state.working_set = envelope.working_set
        self._persist_state(state)

    # ── Model selection helpers ─────────────────────────────────────────────

    def set_pending_model_selection(self, chat_id: int, mode: str) -> None:
        self.pending_model_selections[chat_id] = mode

    def get_pending_model_selection(self, chat_id: int) -> Optional[str]:
        return self.pending_model_selections.get(chat_id)

    def clear_pending_model_selection(self, chat_id: int) -> None:
        self.pending_model_selections.pop(chat_id, None)

    # ── Loop detection ────────────────────────────────────────────────────────

    def record_empty_response(self, chat_id: int) -> bool:
        state = self.get_or_create_session(chat_id)
        state.consecutive_empty_responses += 1
        state.touch()
        self._persist_state(state)
        if state.consecutive_empty_responses >= 2:
            _logger.error(
                f"LOOP DETECTED: {state.consecutive_empty_responses} consecutive empty "
                f"responses for chat {chat_id}. Killing bot to prevent infinite loop."
            )
            return True
        return False

    def reset_empty_response_counter(self, chat_id: int) -> None:
        state = self.get_or_create_session(chat_id)
        state.consecutive_empty_responses = 0
        state.touch()
        self._persist_state(state)

    def check_loop_detected(self, chat_id: int) -> bool:
        state = self.get_or_create_session(chat_id)
        return state.consecutive_empty_responses >= 2


session_manager = SessionManager()
