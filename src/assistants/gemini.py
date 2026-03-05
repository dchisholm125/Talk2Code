import logging
from typing import List, Optional
from assistants.base import CodingAssistant, StreamEvent, StreamEventType

logger = logging.getLogger(__name__)

class GeminiAssistant(CodingAssistant):
    def __init__(self):
        super().__init__("gemini")
        # Technical IDs discovered in gemini-cli-core
        self.models = ["gemini-3-flash-preview", "gemini-3-pro-preview"]
        self.current_model = self.models[0]

    def get_command(self, prompt: str, **kwargs) -> List[str]:
        # Using -p for non-interactive prompt and --accept-raw-output-risk to bypass potential blocks
        cmd = ["gemini", "--model", self.current_model, "--prompt", prompt, "--accept-raw-output-risk"]
        if kwargs.get("format_json"):
            cmd.extend(["--output-format", "stream-json"])
        else:
            cmd.extend(["--output-format", "text"])
        return cmd

    def get_model(self) -> str:
        mapping = {
            "gemini-3-flash-preview": "Gemini 3 Flash",
            "gemini-3-pro-preview": "Gemini 3 Pro"
        }
        return mapping.get(self.current_model, self.current_model)

    def rotate_model(self) -> bool:
        """Rotates to the next model. Returns True if rotated, False if at end of list."""
        try:
            current_idx = self.models.index(self.current_model)
            if current_idx < len(self.models) - 1:
                self.current_model = self.models[current_idx + 1]
                logger.info(f"Rotated Gemini model to: {self.current_model}")
                return True
        except ValueError:
            self.current_model = self.models[0]
            return True
        return False

    def handle_json_event(self, data: dict) -> Optional[StreamEvent]:
        t = data.get("type", "").lower()

        if t == "message":
            content = data.get("content", "")
            is_delta = data.get("delta", False)
            role = data.get("role")
            ev_type = StreamEventType.REASONING if (role == "assistant" and is_delta) else StreamEventType.TEXT
            return self._make_text_event(ev_type, data, ("content", "text"))

        if t == "tool_use":
            return self._make_tool_use_event(data, name_keys=("tool_name", "name"), input_keys=("parameters", "input"))

        if t == "tool_result":
            return self._make_tool_result_event(data, ("output", "result", "text"))

        if t == "result":
            return StreamEvent(StreamEventType.FINISHED)

        return None
