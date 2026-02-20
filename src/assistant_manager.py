import logging
from typing import Dict, Optional, List

from assistants.base import CodingAssistant
from assistants.opencode import OpenCodeAssistant
from assistants.gemini import GeminiAssistant

logger = logging.getLogger(__name__)

class AssistantManager:
    """Centralized core coding assistant manager to swap assistants on-demand."""
    
    def __init__(self):
        self._assistants: Dict[str, CodingAssistant] = {}
        self._default: Optional[str] = None

    def register(self, assistant: CodingAssistant, is_default: bool = False):
        self._assistants[assistant.name.lower()] = assistant
        if is_default or not self._default:
            self._default = assistant.name.lower()

    def set_default(self, name: str):
        if name.lower() in self._assistants:
            self._default = name.lower()
        else:
            raise ValueError(f"Assistant '{name}' is not registered.")

    def get_default_assistant(self) -> CodingAssistant:
        if not self._default:
            raise ValueError("No default assistant set.")
        return self._assistants[self._default]

    def get_assistant(self, name: str) -> Optional[CodingAssistant]:
        return self._assistants.get(name.lower())

    def get_all_assistants(self) -> List[str]:
        return list(self._assistants.keys())

# Provide a global instance for the application to use
manager = AssistantManager()

# Default registered assistants
manager.register(GeminiAssistant(), is_default=True)
manager.register(OpenCodeAssistant())
