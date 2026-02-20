from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

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
        return None

    def rotate_model(self) -> bool:
        """Rotates to the next model if supported. Returns True if rotated."""
        return False

    def is_rate_limit_error(self, stderr: str) -> bool:
        """Checks if the stderr contains a rate limit or auth error."""
        stderr_low = stderr.lower()
        return any(err in stderr_low for err in ["429", "401", "402", "rate limit", "unauthorized", "quota"])
