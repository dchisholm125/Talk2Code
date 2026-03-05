"""
Progress tracking service with event publishing for workflow updates.

This module now exposes:
  * ProcessingStage: a well-known set of internal steps.
  * ProgressTracker: stage-aware emitter that builds `ProgressUpdate` events and feeds
    an optional sink such as the observability hub.
  * Legacy helpers were preserved so that existing audit tooling (stage estimators,
    heartbeat managers) can continue to rely on familiar enums.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

from core.events import (
    ProgressUpdate,
    VisualIndicators,
    VisualState,
    WorkflowState,
    SessionID,
    visual_indicators_for_workflow,
    visual_state_for_workflow,
)
from core.interfaces import ProgressPayload

ProgressEventSink = Callable[[ProgressUpdate], Awaitable[None]]


class ProcessingStage(Enum):
    IDLE = "idle"
    COMPRESSING = "compressing"
    INVOKING_ASSISTANT = "invoking_assistant"
    THINKING = "thinking"
    WRITING = "writing"
    TOOL_EXECUTION = "tool_execution"
    EXECUTING_CODE = "executing_code"
    SUMMARIZING = "summarizing"
    COMPLETE = "complete"
    ERROR = "error"


_STAGE_TO_WORKFLOW: dict[ProcessingStage, WorkflowState] = {
    ProcessingStage.COMPRESSING: WorkflowState.THINKING,
    ProcessingStage.INVOKING_ASSISTANT: WorkflowState.THINKING,
    ProcessingStage.THINKING: WorkflowState.THINKING,
    ProcessingStage.WRITING: WorkflowState.CODING,
    ProcessingStage.TOOL_EXECUTION: WorkflowState.EXECUTING,
    ProcessingStage.EXECUTING_CODE: WorkflowState.EXECUTING,
    ProcessingStage.SUMMARIZING: WorkflowState.EXECUTING,
}


def workflow_state_from_stage(stage: Optional[ProcessingStage]) -> WorkflowState:
    if stage in _STAGE_TO_WORKFLOW:
        return _STAGE_TO_WORKFLOW[stage]  # type: ignore[index]
    if stage == ProcessingStage.ERROR:
        return WorkflowState.EXECUTING
    return WorkflowState.THINKING


@dataclass
class StageProgress:
    stage: ProcessingStage
    progress: float = 0.0
    message: str = ""
    started_at: float = field(default_factory=time.time)
    token_count: int = 0


class ProgressTracker:
    def __init__(self, event_sink: Optional[ProgressEventSink] = None) -> None:
        self.current_stage: Optional[ProcessingStage] = ProcessingStage.IDLE
        self.progress: float = 0.0
        self.message: str = "Ready"
        self.started_at: float = time.time()
        self.token_count: int = 0
        self._previous_stage: Optional[ProcessingStage] = None
        self._stage_metadata: Dict[str, Any] = {}
        self._event_sink = event_sink

    def start_stage(self, stage: ProcessingStage, message: str = "", **metadata) -> None:
        self._previous_stage = self.current_stage
        self.current_stage = stage
        self.progress = 0.0
        self.message = message or self._get_default_message(stage)
        self.started_at = time.time()
        self._stage_metadata = metadata
        self.token_count = 0

    def update_progress(self, progress: float, message: str = "", **metadata) -> None:
        self.progress = max(0.0, min(1.0, progress))
        if message:
            self.message = message
        self._stage_metadata.update(metadata)

    def increment_tokens(self) -> None:
        self.token_count += 1

    def set_token_count(self, count: int) -> None:
        self.token_count = count

    def complete_stage(self) -> None:
        self._previous_stage = self.current_stage
        self.current_stage = ProcessingStage.COMPLETE
        self.progress = 1.0

    def mark_error(self, error_message: str = "") -> None:
        self.current_stage = ProcessingStage.ERROR
        self.message = error_message or "Error occurred"

    def get_elapsed_time(self) -> float:
        return time.time() - self.started_at

    @property
    def started_time(self) -> float:
        return self.started_at

    def get_stage_display(self) -> str:
        if self.current_stage is None:
            return "Unknown"
        if self.current_stage == ProcessingStage.IDLE:
            return "Ready"
        elif self.current_stage == ProcessingStage.COMPLETE:
            return "Complete"
        elif self.current_stage == ProcessingStage.ERROR:
            return f"Error: {self.message}"
        else:
            stage_name = self.current_stage.value.replace("_", " ").title()
            if self.progress > 0:
                return f"{stage_name}: {self.progress:.0%}"
            return stage_name

    def get_formatted_status(self) -> str:
        display = self.get_stage_display()
        elapsed = int(time.time() - self.started_at)

        status_parts = [display]
        if self.token_count > 0:
            status_parts.append(f"tokens: {self.token_count}")
        if elapsed >= 5:
            status_parts.append(f"wait: {elapsed}s")

        return " | ".join(status_parts)

    async def emit_update(
        self,
        stage: Optional[WorkflowState] = None,
        payload: Optional[ProgressPayload] = None,
        message: Optional[str] = None,
        progress: Optional[float] = None,
        tokens: Optional[int] = None,
        eta_seconds: Optional[int] = None,
        visual_state: Optional[VisualState] = None,
        visual_indicators: Optional[VisualIndicators] = None,
        metadata: Optional[Dict[str, Any]] = None,
        complexity_label: Optional[str] = None,
        complexity_score: Optional[float] = None,
        session_id: Optional[SessionID] = None,
    ) -> ProgressUpdate:
        resolved_stage = stage or workflow_state_from_stage(self.current_stage)
        resolved_progress = (
            progress
            if progress is not None
            else payload.progress
            if payload
            else self.progress
        )
        resolved_message = message or (payload.body if payload else self.message)
        resolved_tokens = (
            tokens if tokens is not None else (payload.tokens if payload else self.token_count)
        )
        resolved_elapsed = (
            int(payload.elapsed) if payload and payload.elapsed is not None else int(self.get_elapsed_time())
        )
        resolved_eta = (
            eta_seconds if eta_seconds is not None else (payload.eta_seconds if payload else None)
        )
        combined_metadata: Dict[str, Any] = dict(self._stage_metadata)
        if metadata:
            combined_metadata.update(metadata)

        event = ProgressUpdate(
            stage=resolved_stage,
            progress=resolved_progress,
            elapsed_s=resolved_elapsed,
            eta_seconds=resolved_eta,
            tokens=resolved_tokens,
            message=resolved_message,
            visual_state=visual_state or visual_state_for_workflow(resolved_stage),
            visual_indicators=visual_indicators or visual_indicators_for_workflow(resolved_stage),
            metadata=combined_metadata,
            complexity_label=complexity_label,
            complexity_score=complexity_score,
            session_id=session_id,
        )

        if self._event_sink:
            await self._event_sink(event)

        return event

    @staticmethod
    def _get_default_message(stage: ProcessingStage) -> str:
        messages = {
            ProcessingStage.COMPRESSING: "Compressing conversation...",
            ProcessingStage.INVOKING_ASSISTANT: "Invoking assistant...",
            ProcessingStage.THINKING: "Thinking...",
            ProcessingStage.WRITING: "Writing response...",
            ProcessingStage.TOOL_EXECUTION: "Executing tool...",
            ProcessingStage.EXECUTING_CODE: "Running code...",
            ProcessingStage.SUMMARIZING: "Generating summary...",
            ProcessingStage.COMPLETE: "Done",
            ProcessingStage.ERROR: "Error occurred",
        }
        return messages.get(stage, stage.value)


def create_progress_stages() -> Dict[ProcessingStage, float]:
    return {
        ProcessingStage.COMPRESSING: 0.1,
        ProcessingStage.INVOKING_ASSISTANT: 0.15,
        ProcessingStage.THINKING: 0.3,
        ProcessingStage.WRITING: 0.5,
        ProcessingStage.TOOL_EXECUTION: 0.7,
        ProcessingStage.EXECUTING_CODE: 0.85,
        ProcessingStage.SUMMARIZING: 0.95,
    }
