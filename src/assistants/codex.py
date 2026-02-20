import logging
from typing import List, Optional
from assistants.base import CodingAssistant, StreamEvent, StreamEventType

logger = logging.getLogger(__name__)

class CodexAssistant(CodingAssistant):
    def __init__(self):
        super().__init__("codex")
        self.current_model = "codex-1" # Placeholder model name

    def get_command(self, prompt: str, **kwargs) -> List[str]:
        # Using codex run as per --help discovery
        # If codex doesn't support stream-json, we will treat everything as TEXT
        cmd = ["codex", "run", "--full-auto", prompt]
        return cmd

    def get_model(self) -> str:
        return "Codex Agent"

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        # Since we don't know the exact JSON format for Codex yet,
        # we treat everything as streaming text.
        # Once we confirm it supports --output-format json, we can upgrade this.
        return StreamEvent(StreamEventType.TEXT, content=line)
