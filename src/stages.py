"""
Advanced stage tracking with metrics and heartbeats.

This module provides:
- StageTracker: Advanced stage tracking with duration metrics and progress estimation
- HeartbeatManager: Periodic heartbeat for long-running operations

For basic stage types, see progress.py
For ETA estimation, see progress_estimator.py
"""

from enum import Enum
from typing import Optional, Callable, Dict, Any, List
from dataclasses import dataclass, field
import time
import asyncio

from logger import get_logger
from progress import ProcessingStage, ProgressTracker

_logger = get_logger()


@dataclass
class StageMetrics:
    stage: ProcessingStage
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    token_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def duration_ms(self) -> int:
        end = self.end_time or time.time()
        return int((end - self.start_time) * 1000)
    
    @property
    def elapsed_s(self) -> float:
        return time.time() - self.start_time


class StageTracker:
    def __init__(self):
        self._current_stage: Optional[ProcessingStage] = None
        self._current_metrics: Optional[StageMetrics] = None
        self._stage_history: List[StageMetrics] = []
        self._stage_weights: Dict[ProcessingStage, float] = {
            ProcessingStage.COMPRESSING: 0.1,
            ProcessingStage.INVOKING_ASSISTANT: 0.15,
            ProcessingStage.THINKING: 0.25,
            ProcessingStage.WRITING: 0.2,
            ProcessingStage.TOOL_EXECUTION: 0.15,
            ProcessingStage.EXECUTING_CODE: 0.1,
            ProcessingStage.SUMMARIZING: 0.05,
        }
    
    def start_stage(self, stage: ProcessingStage, message: str = "", **metadata) -> StageMetrics:
        if self._current_stage and self._current_metrics:
            self._finalize_current_stage()
        
        self._current_stage = stage
        self._current_metrics = StageMetrics(
            stage=stage,
            start_time=time.time(),
            metadata=metadata
        )
        
        _logger.log_stage_start(stage, message=message, **metadata)
        return self._current_metrics
    
    def _finalize_current_stage(self) -> None:
        if self._current_metrics:
            self._current_metrics.end_time = time.time()
            self._stage_history.append(self._current_metrics)
            _logger.log_stage_complete(
                self._current_metrics.stage,
                duration_ms=self._current_metrics.duration_ms,
                tokens=self._current_metrics.token_count
            )
    
    def update_progress(self, progress: float, **metadata) -> None:
        if self._current_metrics:
            self._current_metrics.metadata.update(metadata)
            _logger.log_stage_progress(self._current_metrics.stage, progress, **metadata)
    
    def add_tokens(self, count: int = 1) -> None:
        if self._current_metrics:
            self._current_metrics.token_count += count
    
    def set_metadata(self, **metadata) -> None:
        if self._current_metrics:
            self._current_metrics.metadata.update(metadata)
    
    def complete_stage(self) -> None:
        if self._current_stage:
            _logger.log_stage_complete(
                self._current_stage,
                duration_ms=self._current_metrics.duration_ms if self._current_metrics else 0,
                tokens=self._current_metrics.token_count if self._current_metrics else 0
            )
        self._finalize_current_stage()
        self._current_stage = None
        self._current_metrics = None
    
    def mark_error(self, error: str) -> None:
        if self._current_stage:
            _logger.log_stage_error(self._current_stage, error)
        self._finalize_current_stage()
        self._current_stage = None
        self._current_metrics = None
    
    @property
    def current_stage(self) -> Optional[ProcessingStage]:
        return self._current_stage
    
    @property
    def current_metrics(self) -> Optional[StageMetrics]:
        return self._current_metrics
    
    @property
    def elapsed_s(self) -> float:
        if self._current_metrics:
            return self._current_metrics.elapsed_s
        return 0.0
    
    @property
    def token_count(self) -> int:
        if self._current_metrics:
            return self._current_metrics.token_count
        return 0
    
    def get_stage_weight(self, stage: ProcessingStage) -> float:
        return self._stage_weights.get(stage, 0.1)
    
    def estimate_overall_progress(self) -> float:
        completed_progress = 0.0
        for metrics in self._stage_history:
            completed_progress += self.get_stage_weight(metrics.stage)
        
        if self._current_stage and self._current_metrics:
            current_weight = self.get_stage_weight(self._current_stage)
            elapsed = self._current_metrics.elapsed_s
            
            estimated_stage_duration = self._estimate_stage_duration(self._current_stage)
            if estimated_stage_duration > 0:
                stage_progress = min(1.0, elapsed / estimated_stage_duration)
            else:
                stage_progress = 0.5
            
            completed_progress += current_weight * stage_progress
        
        return min(1.0, completed_progress)
    
    def _estimate_stage_duration(self, stage: ProcessingStage) -> float:
        stage_durations = {
            ProcessingStage.COMPRESSING: 30.0,
            ProcessingStage.INVOKING_ASSISTANT: 10.0,
            ProcessingStage.THINKING: 60.0,
            ProcessingStage.WRITING: 45.0,
            ProcessingStage.TOOL_EXECUTION: 30.0,
            ProcessingStage.EXECUTING_CODE: 60.0,
            ProcessingStage.SUMMARIZING: 15.0,
        }
        return stage_durations.get(stage, 30.0)
    
    def get_history(self) -> List[StageMetrics]:
        return self._stage_history.copy()
    
    def reset(self) -> None:
        self._current_stage = None
        self._current_metrics = None
        self._stage_history.clear()


class HeartbeatManager:
    def __init__(self, interval_seconds: int = 8):
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._is_running = False
        self._callbacks: List[Callable[[], Dict[str, Any]]] = []
    
    def add_callback(self, callback: Callable[[], Dict[str, Any]]) -> None:
        self._callbacks.append(callback)
    
    async def start(self, stage_tracker: StageTracker) -> None:
        if self._is_running:
            return
        
        self._is_running = True
        
        async def heartbeat_loop():
            while self._is_running:
                await asyncio.sleep(self._interval)
                if not self._is_running:
                    break
                
                elapsed = int(stage_tracker.elapsed_s)
                if elapsed < 10:
                    continue
                
                progress_data = {
                    'stage': stage_tracker.current_stage,
                    'elapsed_s': elapsed,
                    'tokens': stage_tracker.token_count,
                    'progress': stage_tracker.estimate_overall_progress(),
                }
                
                for callback in self._callbacks:
                    try:
                        callback_data = callback()
                        progress_data.update(callback_data)
                    except Exception as e:
                        _logger.warning(f"Heartbeat callback error: {e}")
                
                self._log_heartbeat(progress_data)
        
        self._task = asyncio.create_task(heartbeat_loop())
    
    async def stop(self) -> None:
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
    
    def _log_heartbeat(self, data: Dict[str, Any]) -> None:
        stage_name = data.get('stage')
        if stage_name:
            stage_str = stage_name.value if hasattr(stage_name, 'value') else str(stage_name)
        else:
            stage_str = 'unknown'
        
        eta_seconds = data.get('eta_seconds')
        progress = data.get('progress')
        elapsed_s = data.get('elapsed_s', 0)
        
        _logger.log_heartbeat(
            stage=stage_str,
            elapsed_s=elapsed_s,
            tokens=data.get('tokens', 0),
            eta_seconds=eta_seconds,
            progress=progress
        )
        
        progress_str = f" [{int(progress*100)}%]" if progress is not None else ""
        eta_str = f" ETA: {eta_seconds//60}m{eta_seconds%60}s" if eta_seconds and eta_seconds < 3600 else ""
        print(f"⏳ [{elapsed_s}s] {stage_str}{progress_str}{eta_str}")
    
    @property
    def interval(self) -> int:
        return self._interval
    
    @interval.setter
    def interval(self, value: int) -> None:
        self._interval = max(5, min(30, value))
