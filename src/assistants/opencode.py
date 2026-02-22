import json
import os
import re
from pathlib import Path
from typing import List, Optional
from assistants.base import CodingAssistant, StreamEvent, StreamEventType

# Curated list of OpenCode-compatible models shown in the #model picker.
# Format: (display_label, opencode_model_id)
# All IDs verified against `opencode models` output.
AVAILABLE_MODELS: List[tuple] = [
    # ── Google Gemini ───────────────────────────────────────────────
    ("Gemini 3 Flash Preview    [google]",  "google/gemini-3-flash-preview"),
    ("Gemini 3 Pro Preview      [google]",  "google/gemini-3-pro-preview"),
    ("Gemini 2.5 Pro            [google]",  "google/gemini-2.5-pro"),
    ("Gemini 2.5 Flash          [google]",  "google/gemini-2.5-flash"),
    ("Gemini 2.5 Flash Lite     [google]",  "google/gemini-2.5-flash-lite"),
    ("Gemini 2.0 Flash          [google]",  "google/gemini-2.0-flash"),
    ("Gemini 2.0 Flash Lite     [google]",  "google/gemini-2.0-flash-lite"),
    # ── OpenAI Codex / GPT ─────────────────────────────────────────
    ("GPT-5.1 Codex Mini        [openai]",  "openai/gpt-5.1-codex-mini"),
    ("GPT-5.1 Codex             [openai]",  "openai/gpt-5.1-codex"),
    ("GPT-5.1 Codex Max         [openai]",  "openai/gpt-5.1-codex-max"),
    ("GPT-5 Codex               [openai]",  "openai/gpt-5-codex"),
    ("GPT-5.2                   [openai]",  "openai/gpt-5.2"),
    # ── OpenCode free tier ─────────────────────────────────────────
    ("Minimax M2.5 (free)       [opencode]", "opencode/minimax-m2.5-free"),
    ("Trinity Large Preview     [opencode]", "opencode/trinity-large-preview-free"),
    ("GPT-5 Nano (free)         [opencode]", "opencode/gpt-5-nano"),
]

_DEFAULT_PLAN_MODEL  = "google/gemini-3-flash-preview"
_DEFAULT_BUILD_MODEL = "openai/gpt-5.1-codex-mini"


def _update_env_key(env_path: Path, key: str, value: str) -> None:
    """Write or overwrite a KEY=value line in the given .env file."""
    text = env_path.read_text() if env_path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(replacement, text)
    else:
        text = text.rstrip("\n") + f"\n{replacement}\n"
    env_path.write_text(text)
    # Also update the live process environment
    os.environ[key] = value


class OpenCodeAssistant(CodingAssistant):
    def __init__(self):
        super().__init__("opencode")
        self.plan_model  = os.getenv("OPENCODE_PLAN_MODEL",  _DEFAULT_PLAN_MODEL)
        self.build_model = os.getenv("OPENCODE_BUILD_MODEL", _DEFAULT_BUILD_MODEL)
        # current_model kept for compatibility with base-class callers
        self.current_model = self.build_model

    # ------------------------------------------------------------------
    # Model accessors / mutators
    # ------------------------------------------------------------------

    def get_plan_model(self) -> str:
        return self.plan_model

    def get_build_model(self) -> str:
        return self.build_model

    def get_model(self) -> str:
        """Returns the build model (used in attribution footers)."""
        return self.build_model

    def set_plan_model(self, model_id: str, env_path: Optional[Path] = None) -> None:
        self.plan_model = model_id
        self.current_model = self.build_model  # keep build as default
        if env_path:
            _update_env_key(env_path, "OPENCODE_PLAN_MODEL", model_id)

    def set_build_model(self, model_id: str, env_path: Optional[Path] = None) -> None:
        self.build_model = model_id
        self.current_model = model_id
        if env_path:
            _update_env_key(env_path, "OPENCODE_BUILD_MODEL", model_id)

    # ------------------------------------------------------------------
    # Command builder
    # ------------------------------------------------------------------

    def get_command(self, prompt: str, agent: str = "coder", model: Optional[str] = None, **kwargs) -> List[str]:
        if model:
            use_model = model
        elif agent == "plan":
            use_model = self.plan_model
        else:
            use_model = self.build_model

        # "coder" is OpenCode's default agent — don't pass --agent to avoid
        # "agent not found" warnings. Only pass --agent for explicitly named agents.
        cmd = ["opencode", "run", "-m", use_model, "--thinking"]
        if agent and agent != "coder":
            cmd.extend(["--agent", agent])
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
