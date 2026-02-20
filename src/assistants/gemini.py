import json
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

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        try:
            data = json.loads(line)
            t = data.get("type", "").lower()

            if t == "message":
                content = data.get("content", "")
                is_delta = data.get("delta", False)
                role = data.get("role")
                
                # Treat assistant delta as reasoning/thinking
                ev_type = StreamEventType.REASONING if (role == "assistant" and is_delta) else StreamEventType.TEXT
                return StreamEvent(ev_type, content=content)
            
            elif t == "tool_use":
                return StreamEvent(StreamEventType.TOOL_USE, metadata={
                    "name": data.get("tool_name", "tool"),
                    "input": data.get("parameters", "")
                })
            
            elif t == "tool_result":
                res_text = data.get("output") or ""
                if data.get("error"):
                    err_msg = data.get("error", {}).get("message", "Unknown error")
                    res_text += f"\nError: {err_msg}"
                return StreamEvent(StreamEventType.TOOL_RESULT, content=res_text)
            
            elif t == "result":
                return StreamEvent(StreamEventType.FINISHED)

        except json.JSONDecodeError:
            return StreamEvent(StreamEventType.TEXT, content=line)
        return None
