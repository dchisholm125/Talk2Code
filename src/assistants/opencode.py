import json
from typing import List, Optional
from assistants.base import CodingAssistant, StreamEvent, StreamEventType

class OpenCodeAssistant(CodingAssistant):
    def __init__(self):
        super().__init__("opencode")
        self.current_model = "opencode/minimax-m2.5-free"

    def get_command(self, prompt: str, agent: str = "coder", model: Optional[str] = None, **kwargs) -> List[str]:
        use_model = model or self.current_model
        cmd = ["opencode", "run", "--agent", agent, "-m", use_model, "--thinking"]
        if kwargs.get("format_json"):
            cmd.extend(["--format", "json"])
        cmd.append(prompt)
        return cmd

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        try:
            data = json.loads(line)
            t = data.get("type", "").lower()
            part = data.get("part", {})

            if t == "reasoning":
                return StreamEvent(StreamEventType.REASONING, content=part.get("text", ""))
            elif t == "text":
                return StreamEvent(StreamEventType.TEXT, content=part.get("text", ""))
            elif t == "tool_use":
                return StreamEvent(StreamEventType.TOOL_USE, metadata={
                    "name": part.get("name", "tool"),
                    "input": part.get("input", "")
                })
            elif t == "tool_result":
                return StreamEvent(StreamEventType.TOOL_RESULT, content=part.get("output", ""))
        except json.JSONDecodeError:
            # Fallback for non-JSON lines (straight text from stdout/stderr)
            return StreamEvent(StreamEventType.TEXT, content=line)
        return None
