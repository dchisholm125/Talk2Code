import os
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

_DEFAULT_PLAN_MODELS = [
    "google/gemini-3.1-pro-preview"
]

_DEFAULT_BUILD_MODELS = [
    "openai/gpt-5.1-codex-mini",
    "openai/gpt-5.1-codex",
    "openai/gpt-5.1-codex-max",
]


class OpenCodeAssistant(CodingAssistant):
    def __init__(self):
        super().__init__("opencode")
        env_plan = os.getenv("OPENCODE_PLAN_MODEL")
        if env_plan:
            self.plan_models = [env_plan] + [m for m in _DEFAULT_PLAN_MODELS if m != env_plan]
        else:
            self.plan_models = _DEFAULT_PLAN_MODELS.copy()

        env_build = os.getenv("OPENCODE_BUILD_MODEL")
        if env_build:
            self.build_models = [env_build] + [m for m in _DEFAULT_BUILD_MODELS if m != env_build]
        else:
            self.build_models = _DEFAULT_BUILD_MODELS.copy()

        self.plan_index = 0
        self.build_index = 0
        self.current_model = self.build_models[0]

    # ------------------------------------------------------------------
    # Model accessors / mutators
    # ------------------------------------------------------------------

    def get_plan_model(self) -> str:
        return self.plan_models[self.plan_index]

    def get_build_model(self) -> str:
        return self.build_models[self.build_index]

    def get_model(self) -> str:
        """Returns the build model (used in attribution footers)."""
        return self.build_models[self.build_index]

    def set_plan_model(self, model_id: str, env_path: Optional[Path] = None) -> None:
        if model_id in self.plan_models:
            self.plan_index = self.plan_models.index(model_id)
        else:
            self.plan_models.insert(0, model_id)
            self.plan_index = 0
        
        self.current_model = self.build_models[self.build_index]  # keep build as default
        if env_path:
            self.update_env_key(env_path, "OPENCODE_PLAN_MODEL", model_id)

    def set_build_model(self, model_id: str, env_path: Optional[Path] = None) -> None:
        if model_id in self.build_models:
            self.build_index = self.build_models.index(model_id)
        else:
            self.build_models.insert(0, model_id)
            self.build_index = 0
            
        self.current_model = model_id
        if env_path:
            self.update_env_key(env_path, "OPENCODE_BUILD_MODEL", model_id)

    def rotate_model(self, agent: str = "coder") -> bool:
        if agent == "plan":
            if self.plan_index < len(self.plan_models) - 1:
                self.plan_index += 1
                self.current_model = self.plan_models[self.plan_index]
                return True
        else:
            if self.build_index < len(self.build_models) - 1:
                self.build_index += 1
                self.current_model = self.build_models[self.build_index]
                return True
        return False

    # ------------------------------------------------------------------
    # Command builder
    # ------------------------------------------------------------------

    def get_command(self, prompt: str, agent: str = "coder", model: Optional[str] = None, **kwargs) -> List[str]:
        if model:
            use_model = model
        elif agent == "plan":
            use_model = self.get_plan_model()
        else:
            use_model = self.get_build_model()

        # "coder" is OpenCode's default agent — don't pass --agent to avoid
        # "agent not found" warnings. Only pass --agent for explicitly named agents.
        cmd = ["opencode", "run", "-m", use_model]
        if agent and agent != "coder":
            cmd.extend(["--agent", agent])
        if kwargs.get("format_json"):
            cmd.extend(["--format", "json"])
        cmd.append(prompt)
        return cmd

    def handle_json_event(self, data: dict) -> Optional[StreamEvent]:
        t = data.get("type", "").lower()
        part = data.get("part", {})

        if t == "reasoning":
            return self._make_text_event(StreamEventType.REASONING, part, ("text", "content"))
        if t == "text":
            return self._make_text_event(StreamEventType.TEXT, part, ("text", "content"))
        if t == "tool_use":
            return self._make_tool_use_event(part)
        if t == "tool_result":
            return self._make_tool_result_event(part, ("output", "result", "content"))
        return None
