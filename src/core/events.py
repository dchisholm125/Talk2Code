"""Domain events emitted by the orchestrator service."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, NewType


class DomainEvent:
    """Marker base class for orchestrator events."""


SessionID = NewType("SessionID", int)


@dataclass
class DiscoveryCircle:
    name: str
    files: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ContextEnvelope:
    intent_summary: str
    entities: List[str]
    circles: List[DiscoveryCircle] = field(default_factory=list)
    git_history: str = ""
    summary_text: str = ""
    working_set: List[str] = field(default_factory=list)


class WorkflowState(Enum):
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    CODING = "coding"
    EXECUTING = "executing"


class VisualState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    CODING = "coding"
    EXECUTING = "executing"
    COMPLETE = "complete"


_WORKFLOW_VISUAL_MAP: dict[WorkflowState, VisualState] = {
    WorkflowState.TRANSCRIBING: VisualState.THINKING,
    WorkflowState.THINKING: VisualState.THINKING,
    WorkflowState.CODING: VisualState.CODING,
    WorkflowState.EXECUTING: VisualState.EXECUTING,
}


def visual_state_for_workflow(state: WorkflowState) -> VisualState:
    return _WORKFLOW_VISUAL_MAP.get(state, VisualState.IDLE)


@dataclass(frozen=True)
class VisualIndicators:
    thinking: bool = False
    coding: bool = False


_WORKFLOW_VISUAL_FLAGS: dict[WorkflowState, VisualIndicators] = {
    WorkflowState.TRANSCRIBING: VisualIndicators(thinking=True),
    WorkflowState.THINKING: VisualIndicators(thinking=True),
    WorkflowState.CODING: VisualIndicators(thinking=True, coding=True),
    WorkflowState.EXECUTING: VisualIndicators(coding=True),
}


def visual_indicators_for_workflow(state: WorkflowState) -> VisualIndicators:
    return _WORKFLOW_VISUAL_FLAGS.get(state, VisualIndicators())


class LifecycleStatus(Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class StateChanged(DomainEvent):
    state: WorkflowState
    details: Optional[str] = None


@dataclass(frozen=True)
class ContentDelta(DomainEvent):
    text: str
    state: WorkflowState


@dataclass(frozen=True)
class ProgressUpdate(DomainEvent):
    stage: WorkflowState
    progress: Optional[float] = None
    elapsed_s: Optional[int] = None
    eta_seconds: Optional[int] = None
    tokens: Optional[int] = None
    complexity_label: Optional[str] = None
    complexity_score: Optional[float] = None
    message: Optional[str] = None
    visual_state: VisualState = VisualState.IDLE
    visual_indicators: VisualIndicators = field(default_factory=VisualIndicators)
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[SessionID] = None


@dataclass(frozen=True)
class TaskInteraction(DomainEvent):
    question: str
    stage: WorkflowState
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LifecycleEvent(DomainEvent):
    status: LifecycleStatus
    message: Optional[str] = None


@dataclass(frozen=True)
class ProcessingFailed(DomainEvent):
    error: str
    stage: WorkflowState
    details: Optional[str] = None
