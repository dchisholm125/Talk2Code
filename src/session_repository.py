from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List

from logger import get_logger

_logger = get_logger()


class SessionRepository(ABC):
    @abstractmethod
    def load_sessions(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def save_sessions(self, sessions: List[Dict[str, Any]]) -> None:
        ...


class JsonSessionRepository(SessionRepository):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_sessions(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []

        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as exc:
            _logger.warning("Failed to load session history", exc_info=exc)
            return []

    def save_sessions(self, sessions: List[Dict[str, Any]]) -> None:
        try:
            with open(self.path, "w") as f:
                json.dump(sessions, f, indent=2)
        except Exception as exc:
            _logger.error("Failed to persist session history", exc_info=exc)
