import json
import logging
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Sequence, Pattern
from enum import Enum

logger = logging.getLogger(__name__)

class StreamEventType(Enum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    FINISHED = "finished"

@dataclass
class StreamEvent:
    type: StreamEventType
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

class CodingAssistant:
    """Base class for coding assistants."""

    CODE_BLOCK_PATTERN: Pattern[str] = re.compile(r"```(?:[\w+\-]*\n)?([\s\S]+?)```")
    INLINE_CODE_PATTERN: Pattern[str] = re.compile(r"`([^`]+)`")
    MARKDOWN_HEADER_PATTERN: Pattern[str] = re.compile(r"^#{1,6}\s*(.*)$", re.MULTILINE)
    _env_line_patterns: Dict[str, Pattern[str]] = {}

    def __init__(self, name: str):
        self.name = name
        self.current_model: Optional[str] = None

    def get_command(self, prompt: str, **kwargs) -> List[str]:
        """Returns the command line list to execute this assistant."""
        raise NotImplementedError

    def get_model(self) -> str:
        return self.current_model or "default"

    def format_prompt(self, window: List[Dict[str, Any]], system_instruction: str, extra_context: str = "") -> str:
        """Formats the conversation window and system instruction into a single string for the CLI."""
        parts = [f"System: {system_instruction}"]
        if extra_context:
            parts.append(f"\n{extra_context}")
        parts.append("\nConversation so far:")
        for entry in window:
            role = "Developer" if entry["role"] == "user" else "Assistant"
            content = entry["content"]
            if entry.get("solo") and entry["role"] == "user":
                content = f"[developer thinking aloud]: {content}"
            parts.append(f"{role}: {content}")
        return "\n\n".join(parts)

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        """Parses a single line of output from the CLI into a standard StreamEvent."""
        data = self._load_json_line(line)
        if data is None:
            return StreamEvent(StreamEventType.TEXT, content=line)
        event = self.handle_json_event(data)
        if event is None:
            return StreamEvent(StreamEventType.TEXT, content=line)
        return event

    def handle_json_event(self, data: Dict[str, Any]) -> Optional[StreamEvent]:
        """Hook for derived classes to map JSON payloads to StreamEvents."""
        return None

    def _load_json_line(self, line: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _extract_first(self, payload: Dict[str, Any], keys: Sequence[str]) -> str:
        for key in keys:
            if key not in payload:
                continue
            value = payload[key]
            if value is None:
                continue
            if isinstance(value, str):
                return value
            try:
                return json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(value)
        return ""

    def _make_text_event(self, event_type: StreamEventType, payload: Dict[str, Any], keys: Sequence[str]) -> StreamEvent:
        text = self._extract_first(payload, keys)
        return StreamEvent(event_type, content=text)

    def _make_tool_use_event(
        self,
        payload: Dict[str, Any],
        name_keys: Sequence[str] = ("name", "tool_name", "toolId"),
        input_keys: Sequence[str] = ("input", "parameters", "args"),
    ) -> StreamEvent:
        tool_name = self._extract_first(payload, name_keys) or "tool"
        tool_input = self._extract_first(payload, input_keys)
        return StreamEvent(StreamEventType.TOOL_USE, metadata={"name": tool_name, "input": tool_input})

    def _make_tool_result_event(
        self,
        payload: Dict[str, Any],
        text_keys: Sequence[str],
        include_error: bool = True,
    ) -> StreamEvent:
        text = self._extract_first(payload, text_keys)
        if include_error:
            text += self._format_error_message(payload)
        return StreamEvent(StreamEventType.TOOL_RESULT, content=text)

    def _format_error_message(self, payload: Dict[str, Any]) -> str:
        error = payload.get("error")
        if not error:
            return ""
        if isinstance(error, str):
            message = error
        elif isinstance(error, dict):
            message = error.get("message") or error.get("details")
        else:
            message = str(error)
        if message:
            return f"\nError: {message}"
        return ""

    @classmethod
    def extract_code_blocks(cls, text: str) -> List[str]:
        return cls.CODE_BLOCK_PATTERN.findall(text)

    @classmethod
    def extract_inline_code(cls, text: str) -> List[str]:
        return cls.INLINE_CODE_PATTERN.findall(text)

    @classmethod
    def extract_markdown_headers(cls, text: str) -> List[str]:
        return cls.MARKDOWN_HEADER_PATTERN.findall(text)

    @classmethod
    def _env_line_pattern(cls, key: str) -> Pattern[str]:
        if key not in cls._env_line_patterns:
            cls._env_line_patterns[key] = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        return cls._env_line_patterns[key]

    def update_env_key(self, env_path: Path, key: str, value: str) -> None:
        """Write or overwrite a KEY=value line in the given .env file."""
        logger.debug("Updating %s in %s", key, env_path)
        text = env_path.read_text() if env_path.exists() else ""
        pattern = self._env_line_pattern(key)
        replacement = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text = text.rstrip("\n") + f"\n{replacement}\n"
        env_path.write_text(text)
        os.environ[key] = value

    def rotate_model(self) -> bool:
        """Rotates to the next model if supported. Returns True if rotated."""
        return False

    def is_rate_limit_error(self, stderr: str) -> bool:
        """Checks if the stderr contains a rate limit or auth error."""
        stderr_low = stderr.lower()
        return any(err in stderr_low for err in ["429", "401", "402", "rate limit", "unauthorized", "quota"])
