"""
ETA estimation and progress prediction.

This module provides:
- ProgressEstimator: Predicts duration based on prompt complexity and historical data
- ProgressUpdate: Formatted progress messages for Telegram

Uses historical timing data to improve predictions over time.
See progress.py for core stage types.
"""

from typing import Optional, Dict, Any, List, Deque
from dataclasses import dataclass, field
from collections import deque
import time
import re

from progress import ProcessingStage
from logger import get_logger

_logger = get_logger()


@dataclass
class TimingSample:
    stage: ProcessingStage
    duration_ms: int
    timestamp: float
    prompt_length: int = 0
    output_length: int = 0


class ProgressEstimator:
    def __init__(self, history_size: int = 50):
        self._history: Deque[TimingSample] = deque(maxlen=history_size)
        self._stage_weights: Dict[ProcessingStage, float] = {
            ProcessingStage.COMPRESSING: 0.10,
            ProcessingStage.INVOKING_ASSISTANT: 0.10,
            ProcessingStage.THINKING: 0.25,
            ProcessingStage.WRITING: 0.25,
            ProcessingStage.TOOL_EXECUTION: 0.15,
            ProcessingStage.EXECUTING_CODE: 0.10,
            ProcessingStage.SUMMARIZING: 0.05,
        }
        
        self._base_durations: Dict[ProcessingStage, float] = {
            ProcessingStage.COMPRESSING: 30.0,
            ProcessingStage.INVOKING_ASSISTANT: 10.0,
            ProcessingStage.THINKING: 45.0,
            ProcessingStage.WRITING: 45.0,
            ProcessingStage.TOOL_EXECUTION: 25.0,
            ProcessingStage.EXECUTING_CODE: 45.0,
            ProcessingStage.SUMMARIZING: 15.0,
        }
        
        self._current_stage: Optional[ProcessingStage] = None
        self._stage_start_time: float = 0.0
        self._stage_token_rate: float = 0.0
        self._estimated_total_duration: float = 0.0
    
    def record_sample(self, stage: ProcessingStage, duration_ms: int, prompt_length: int = 0, output_length: int = 0) -> None:
        sample = TimingSample(
            stage=stage,
            duration_ms=duration_ms,
            timestamp=time.time(),
            prompt_length=prompt_length,
            output_length=output_length
        )
        self._history.append(sample)
        
        self._update_base_duration(stage, duration_ms)
        
        if output_length > 0 and duration_ms > 0:
            self._stage_token_rate = output_length / (duration_ms / 1000.0)
    
    def _update_base_duration(self, stage: ProcessingStage, observed_ms: int) -> None:
        existing = self._base_durations.get(stage, 30.0)
        alpha = 0.3
        self._base_durations[stage] = alpha * (observed_ms / 1000.0) + (1 - alpha) * existing
    
    def set_current_stage(self, stage: ProcessingStage) -> None:
        self._current_stage = stage
        self._stage_start_time = time.time()
        self._estimate_total_duration()
    
    def _estimate_total_duration(self) -> float:
        total = 0.0
        for stage, weight in self._stage_weights.items():
            base = self._base_durations.get(stage, 30.0)
            total += base * weight
        self._estimated_total_duration = total
        return total
    
    def analyze_prompt_complexity(self, prompt: str) -> Dict[str, Any]:
        word_count = len(prompt.split())
        char_count = len(prompt)
        code_blocks = len(re.findall(r'```[\s\S]*?```', prompt))
        has_tech_keywords = bool(re.search(r'\b(function|class|import|def|const|let|var|async|await)\b', prompt))
        has_file_refs = len(re.findall(r'\b\w+\.\w+\b', prompt))
        
        complexity_score = 0.0
        complexity_score += min(1.0, word_count / 100) * 0.3
        complexity_score += min(1.0, code_blocks / 5) * 0.3
        complexity_score += 0.2 if has_tech_keywords else 0.0
        complexity_score += min(1.0, has_file_refs / 10) * 0.2
        
        estimated_duration = self._estimate_total_duration()
        complexity_multiplier = 1.0 + (complexity_score * 0.5)
        
        return {
            'word_count': word_count,
            'char_count': char_count,
            'code_blocks': code_blocks,
            'has_tech_keywords': has_tech_keywords,
            'file_refs': has_file_refs,
            'complexity_score': complexity_score,
            'estimated_duration': estimated_duration * complexity_multiplier,
            'complexity_label': self._complexity_label(complexity_score)
        }
    
    def _complexity_label(self, score: float) -> str:
        if score < 0.2:
            return "simple"
        elif score < 0.4:
            return "moderate"
        elif score < 0.6:
            return "complex"
        else:
            return "very complex"
    
    def get_progress(self) -> Dict[str, Any]:
        if not self._current_stage:
            return {
                'stage': None,
                'progress': 0.0,
                'elapsed_s': 0,
                'eta_seconds': None,
                'tokens': 0,
                'confidence': 'none'
            }
        
        elapsed = time.time() - self._stage_start_time
        
        completed_weight = 0.0
        for stage, weight in self._stage_weights.items():
            if stage == self._current_stage:
                break
            completed_weight += weight
        
        current_weight = self._stage_weights.get(self._current_stage, 0.1)
        base_duration = self._base_durations.get(self._current_stage, 30.0)
        
        if elapsed > 0 and base_duration > 0:
            stage_progress = min(1.0, elapsed / base_duration)
        else:
            stage_progress = 0.5
        
        total_progress = completed_weight + (current_weight * stage_progress)
        
        remaining_weight = 1.0 - total_progress
        remaining_duration = self._estimated_total_duration * remaining_weight
        eta_seconds = int(remaining_duration) if remaining_duration > 0 else None
        
        confidence = self._calculate_confidence()
        
        return {
            'stage': self._current_stage,
            'stage_name': self._current_stage.value if self._current_stage else 'unknown',
            'progress': min(1.0, total_progress),
            'elapsed_s': int(elapsed),
            'eta_seconds': eta_seconds,
            'tokens': 0,
            'confidence': confidence,
            'stage_progress': stage_progress
        }
    
    def _calculate_confidence(self) -> str:
        sample_count = len([s for s in self._history if s.stage == self._current_stage])
        
        if sample_count >= 5:
            return 'high'
        elif sample_count >= 2:
            return 'medium'
        elif sample_count >= 1:
            return 'low'
        else:
            return 'none'
    
    def update_tokens(self, token_count: int) -> None:
        if self._current_stage and token_count > 0:
            elapsed = time.time() - self._stage_start_time
            if elapsed > 0:
                self._stage_token_rate = token_count / elapsed
    
    def estimate_remaining_tokens(self) -> Optional[int]:
        if not self._current_stage or self._stage_token_rate <= 0:
            return None
        
        remaining_weight = 1.0 - self.get_progress()['progress']
        estimated_remaining = remaining_weight * 1000
        
        return int(estimated_remaining / max(0.1, self._stage_token_rate / 100))
    
    def get_historical_average(self, stage: ProcessingStage) -> Optional[float]:
        samples = [s.duration_ms for s in self._history if s.stage == stage]
        if len(samples) >= 2:
            return sum(samples) / len(samples)
        return None
    
    def reset(self) -> None:
        self._current_stage = None
        self._stage_start_time = 0.0
        self._stage_token_rate = 0.0
    
    @property
    def stage_weights(self) -> Dict[ProcessingStage, float]:
        return self._stage_weights.copy()
    
    @property
    def estimated_total_duration(self) -> float:
        return self._estimated_total_duration


class ProgressUpdate:
    def __init__(
        self,
        stage: ProcessingStage,
        progress: float,
        elapsed_s: int,
        eta_seconds: Optional[int] = None,
        tokens: int = 0,
        message: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.stage = stage
        self.progress = progress
        self.elapsed_s = elapsed_s
        self.eta_seconds = eta_seconds
        self.tokens = tokens
        self.message = message
        self.metadata = metadata or {}
        self.timestamp = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'stage': self.stage.value if self.stage else 'unknown',
            'progress': self.progress,
            'elapsed_s': self.elapsed_s,
            'eta_seconds': self.eta_seconds,
            'tokens': self.tokens,
            'message': self.message,
            'metadata': self.metadata,
            'timestamp': self.timestamp
        }
    
    def format_telegram_message(self) -> str:
        stage_name = self.stage.value.replace('_', ' ').title() if self.stage else 'Processing'
        
        parts = [f"<b>{stage_name}</b>"]
        
        progress_pct = int(self.progress * 100)
        parts.append(f"{progress_pct}%")
        
        if self.elapsed_s >= 5:
            parts.append(f"<i>[Wait: {self.elapsed_s}s]</i>")
        
        if self.eta_seconds is not None and self.eta_seconds > 0:
            if self.eta_seconds < 60:
                parts.append(f"ETA: {self.eta_seconds}s")
            else:
                mins = self.eta_seconds // 60
                secs = self.eta_seconds % 60
                parts.append(f"ETA: {mins}m{secs}s")
        
        if self.tokens > 0:
            parts.append(f"Tokens: {self.tokens}")
        
        if self.message:
            parts.append(f"\n\n<code>{self.message}</code>")
        
        return " ".join(parts)
    
    @classmethod
    def from_estimator(cls, estimator: ProgressEstimator, message: str = "") -> 'ProgressUpdate':
        data = estimator.get_progress()
        return cls(
            stage=data['stage'],
            progress=data['progress'],
            elapsed_s=data['elapsed_s'],
            eta_seconds=data['eta_seconds'],
            tokens=data['tokens'],
            message=message
        )
