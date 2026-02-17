import json
import os
import threading
import shutil
from datetime import datetime
from typing import Optional, Literal
from pathlib import Path

Role = Literal["user", "assistant", "tool"]
Channel = Literal["/think", "/code"]

DECISION_TAGS = {"decision", "result", "change", "todo", "plan", "summary"}


class ContextManager:
    """Manages persistent context for maintaining conversation continuity.
    
    Supports multiple channels (/think, /code) with separate threads but
    shared storage. Includes rolling summaries for cross-channel awareness.
    
    Features:
    - Atomic writes with thread locking
    - Auto-migration from old schema with backup
    - Per-chat scope (chat_id + user_id)
    - Tag-based cross-channel filtering
    """

    def __init__(self, history_path: str = "context_history.json", max_entries: int = 10):
        self.history_path = Path(history_path)
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._ensure_history_file()

    def _migrate_if_needed(self, data: dict) -> dict:
        """Detect and migrate old schema to new format."""
        if "entries" not in data:
            old_path = self.history_path.with_suffix('.v1.backup.json')
            shutil.copy(self.history_path, old_path)
            entries = data if isinstance(data, list) else []
            return {"entries": entries, "summaries": {}, "metadata": {"migrated_from": str(old_path)}}
        return data

    def _ensure_history_file(self) -> None:
        """Create history file if it doesn't exist, handle migration."""
        if self.history_path.exists():
            with self._lock:
                try:
                    with open(self.history_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    data = self._migrate_if_needed(data)
                    self._write_data_atomic(data)
                except (json.JSONDecodeError, ValueError):
                    backup = self.history_path.with_suffix('.json.corrupt.backup')
                    shutil.move(self.history_path, backup)
                    self._write_data_atomic({"entries": [], "summaries": {}, "metadata": {"recovery": str(backup)}})
        else:
            self._write_data_atomic({"entries": [], "summaries": {}})

    def _read_data(self) -> dict:
        """Read all data from the history file."""
        try:
            with open(self.history_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"entries": [], "summaries": {}}

    def _write_data_atomic(self, data: dict) -> None:
        """Atomic write: write to temp file, then rename."""
        tmp_path = self.history_path.with_suffix('.json.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.rename(self.history_path)

    def _write_data(self, data: dict) -> None:
        """Thread-safe atomic write."""
        with self._lock:
            self._write_data_atomic(data)

    def save_context(
        self,
        role: Role,
        content: str,
        channel: Channel = "/think",
        thread_id: str = "default",
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        tags: Optional[set] = None
    ) -> None:
        """Append a new entry to the history file with timestamp."""
        with self._lock:
            data = self._read_data()
            entries = data.get("entries", [])
            
            entry = {
                "ts": datetime.now().isoformat(timespec='seconds'),
                "role": role,
                "channel": channel,
                "thread_id": thread_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "content": content,
                "meta": {"tags": list(tags) if tags else []}
            }
            
            entries.append(entry)
            
            if len(entries) > self.max_entries:
                entries = entries[-self.max_entries:]
            
            data["entries"] = entries
            self._write_data_atomic(data)

    def get_recent_context(
        self,
        limit: int = 3,
        channel: Optional[Channel] = None,
        thread_id: Optional[str] = None,
        chat_id: Optional[int] = None
    ) -> str:
        """Load and return the latest `limit` entries as formatted context."""
        data = self._read_data()
        entries = data.get("entries", [])
        
        if not entries:
            return ""
        
        filtered = entries
        if channel:
            filtered = [e for e in filtered if e.get("channel") == channel]
        if thread_id:
            filtered = [e for e in filtered if e.get("thread_id") == thread_id]
        if chat_id:
            filtered = [e for e in filtered if e.get("chat_id") == chat_id]
        
        recent = filtered[-limit:] if len(filtered) > limit else filtered
        
        context_parts = []
        for entry in recent:
            ts = entry.get("ts", "unknown")
            role = entry.get("role", "user")
            ch = entry.get("channel", "")
            content = entry.get("content", "")[:200]
            context_parts.append(f"[{ts}] {ch} {role}: {content}")
        
        return "\n".join(context_parts)

    def get_context_summary(
        self,
        limit: int = 3,
        channel: Optional[Channel] = None,
        thread_id: Optional[str] = None,
        chat_id: Optional[int] = None
    ) -> str:
        """Get recent context formatted for prepending to prompts."""
        recent = self.get_recent_context(limit, channel, thread_id, chat_id)
        
        if not recent:
            return ""
        
        return f"Recent conversation:\n{recent}\n\n"

    def get_cross_channel_glimpse(
        self,
        other_channel: Channel,
        limit: int = 1,
        tagged_only: bool = True
    ) -> str:
        """Get a brief glimpse of the other channel's recent activity.
        
        Args:
            other_channel: The channel to peek into
            limit: Max entries to include
            tagged_only: If True, only include entries with decision tags
        """
        data = self._read_data()
        entries = data.get("entries", [])
        
        other_entries = [
            e for e in entries 
            if e.get("channel") == other_channel and e.get("role") == "assistant"
        ]
        
        if tagged_only:
            other_entries = [
                e for e in other_entries
                if any(t in DECISION_TAGS for t in e.get("meta", {}).get("tags", []))
            ]
        
        if not other_entries:
            return ""
        
        recent = other_entries[-limit:]
        parts = []
        for entry in recent:
            content = entry.get("content", "")[:150]
            parts.append(f"[{other_channel}] {content}")
        
        return "\n".join(parts)

    def get_summary(self, thread_id: str = "default", chat_id: Optional[int] = None) -> str:
        """Get the rolling summary for a thread."""
        data = self._read_data()
        summaries = data.get("summaries", {})
        key = f"{chat_id}:{thread_id}" if chat_id else thread_id
        return summaries.get(key, "")

    def update_summary(self, summary: str, thread_id: str = "default", chat_id: Optional[int] = None) -> None:
        """Update the rolling summary for a thread."""
        with self._lock:
            data = self._read_data()
            if "summaries" not in data:
                data["summaries"] = {}
            key = f"{chat_id}:{thread_id}" if chat_id else thread_id
            data["summaries"][key] = summary
            self._write_data_atomic(data)

    def clear_history(self, chat_id: Optional[int] = None, thread_id: Optional[str] = None) -> int:
        """Clear history entries. If chat_id/thread_id provided, clear only those."""
        with self._lock:
            data = self._read_data()
            entries = data.get("entries", [])
            
            if chat_id is None and thread_id is None:
                old_count = len(entries)
                data["entries"] = []
                self._write_data_atomic(data)
                return old_count
            
            original_count = len(entries)
            data["entries"] = [
                e for e in entries
                if (chat_id and e.get("chat_id") != chat_id) or (thread_id and e.get("thread_id") != thread_id)
            ]
            self._write_data_atomic(data)
            return original_count - len(data["entries"])

    def get_entry_count(self, chat_id: Optional[int] = None) -> int:
        """Return the number of stored entries."""
        data = self._read_data()
        entries = data.get("entries", [])
        if chat_id:
            entries = [e for e in entries if e.get("chat_id") == chat_id]
        return len(entries)
