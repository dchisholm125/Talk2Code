import os
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
from collections import deque
from logging.handlers import RotatingFileHandler
import time as time_module


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

DEFAULT_LOG_LEVEL = LogLevel.INFO
DEFAULT_LOG_PATH = Path.home() / ".voice-to-code" / "app.log"

VERBOSE_LOG_ENV = os.getenv("VERBOSE_LOGGING", "false").lower() in ("true", "1", "yes")
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "8"))


class VoiceToCodeLogger:
    _instance: Optional['VoiceToCodeLogger'] = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if VoiceToCodeLogger._initialized:
            return
        
        self._setup_logging()
        self._setup_verbose_logging()
        self._setup_timing_tracking()
        VoiceToCodeLogger._initialized = True
    
    def _setup_logging(self) -> None:
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        
        log_path_str = os.getenv("LOG_PATH", str(DEFAULT_LOG_PATH))
        log_path = Path(log_path_str)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.file_handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        self.file_handler.setLevel(log_level)
        
        self.console_handler = logging.StreamHandler(sys.stdout)
        self.console_handler.setLevel(log_level)
        
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        self.file_handler.setFormatter(formatter)
        self.console_handler.setFormatter(formatter)
        
        self.logger = logging.getLogger("voice-to-code")
        self.logger.setLevel(log_level)
        self.logger.addHandler(self.file_handler)
        self.logger.addHandler(self.console_handler)
        
        self._log_level = log_level
        self._verbose = VERBOSE_LOG_ENV
    
    def _setup_verbose_logging(self) -> None:
        self._verbose = VERBOSE_LOG_ENV
        if self._verbose:
            self.logger.info("Verbose logging enabled")
    
    def _setup_timing_tracking(self) -> None:
        self._stage_timings: Dict[str, float] = {}
        self._stage_history: deque = deque(maxlen=100)
        self._operation_timings: Dict[str, List[float]] = {}
    
    @property
    def verbose(self) -> bool:
        return self._verbose
    
    @property
    def logger(self) -> logging.Logger:
        return self._logger
    
    @logger.setter
    def logger(self, value: logging.Logger):
        self._logger = value
    
    def set_level(self, level: str) -> None:
        log_level = getattr(logging, level.upper(), logging.INFO)
        self._log_level = log_level
        self.logger.setLevel(log_level)
        self.file_handler.setLevel(log_level)
        self.console_handler.setLevel(log_level)
    
    def debug(self, msg: str, **kwargs) -> None:
        self.logger.debug(self._format_message(msg, **kwargs))
    
    def info(self, msg: str, **kwargs) -> None:
        self.logger.info(self._format_message(msg, **kwargs))
    
    def warning(self, msg: str, **kwargs) -> None:
        self.logger.warning(self._format_message(msg, **kwargs))
    
    def error(self, msg: str, **kwargs) -> None:
        self.logger.error(self._format_message(msg, **kwargs))
    
    def critical(self, msg: str, **kwargs) -> None:
        self.logger.critical(self._format_message(msg, **kwargs))
    
    def log_exception(self, msg: str, **kwargs) -> None:
        self.logger.exception(self._format_message(msg, **kwargs))
    
    def _format_message(self, msg: str, **kwargs) -> str:
        if not kwargs:
            return msg
        
        timing_info = []
        if 'duration_ms' in kwargs:
            timing_info.append(f"duration={kwargs.pop('duration_ms')}ms")
        if 'elapsed_s' in kwargs:
            timing_info.append(f"elapsed={kwargs.pop('elapsed_s')}s")
        if 'tokens' in kwargs:
            timing_info.append(f"tokens={kwargs.pop('tokens')}")
        if 'progress' in kwargs:
            timing_info.append(f"progress={kwargs.pop('progress')}")
        if 'eta' in kwargs:
            timing_info.append(f"eta={kwargs.pop('eta')}")
        
        extra = ", ".join(timing_info)
        if extra:
            return f"{msg} [{extra}]"
        return msg
    
    def log_stage_start(self, stage, **metadata) -> None:
        stage_str = stage.value if hasattr(stage, 'value') else str(stage)
        start_time = time_module.time()
        self._stage_timings[stage_str] = start_time
        
        metadata_str = ""
        if metadata:
            metadata_str = " | " + ", ".join(f"{k}={v}" for k, v in metadata.items())
        
        self.info(f"STAGE_START: {stage_str}{metadata_str}")
        if self._verbose:
            self.debug(f"STAGE_START_DETAIL: {stage_str} | timestamp={start_time:.3f}")
    
    def log_stage_progress(self, stage, progress: float, **metadata) -> None:
        stage_str = stage.value if hasattr(stage, 'value') else str(stage)
        self.debug(f"STAGE_PROGRESS: {stage_str} | {progress:.0%}")
        
        if self._verbose:
            start_time = self._stage_timings.get(stage_str)
            if start_time:
                elapsed = time_module.time() - start_time
                metadata_str = ", ".join(f"{k}={v}" for k, v in metadata.items()) if metadata else ""
                self.debug(f"STAGE_PROGRESS_DETAIL: {stage_str} | progress={progress:.2%} | elapsed={elapsed:.1f}s | {metadata_str}")
    
    def log_stage_complete(self, stage, **metadata) -> None:
        stage_str = stage.value if hasattr(stage, 'value') else str(stage)
        
        start_time = self._stage_timings.pop(stage_str, None)
        duration_ms = 0
        if start_time:
            duration_ms = int((time_module.time() - start_time) * 1000)
            self._record_timing(stage_str, duration_ms)
        
        metadata_str = ""
        if metadata:
            metadata_parts = [f"{k}={v}" for k, v in metadata.items()]
            if duration_ms:
                metadata_parts.append(f"duration={duration_ms}ms")
            metadata_str = " | " + ", ".join(metadata_parts)
        
        self.info(f"STAGE_COMPLETE: {stage_str}{metadata_str}")
        
        if self._verbose and duration_ms:
            self.debug(f"STAGE_COMPLETE_DETAIL: {stage_str} | duration_ms={duration_ms}")
    
    def log_stage_error(self, stage, error: str, **metadata) -> None:
        stage_str = stage.value if hasattr(stage, 'value') else str(stage)
        
        start_time = self._stage_timings.pop(stage_str, None)
        duration_ms = 0
        if start_time:
            duration_ms = int((time_module.time() - start_time) * 1000)
        
        metadata_str = f"error={error}"
        if duration_ms:
            metadata_str += f", duration={duration_ms}ms"
        
        self.error(f"STAGE_ERROR: {stage_str} | {metadata_str}")
    
    def log_heartbeat(self, stage: str, elapsed_s: int, tokens: int = 0, eta_seconds: Optional[int] = None, progress: Optional[float] = None) -> None:
        parts = [f"heartbeat | stage={stage}", f"elapsed={elapsed_s}s"]
        
        if tokens > 0:
            parts.append(f"tokens={tokens}")
        
        if progress is not None:
            parts.append(f"progress={progress:.0%}")
        
        if eta_seconds is not None:
            if eta_seconds < 60:
                parts.append(f"eta={eta_seconds}s")
            else:
                eta_mins = eta_seconds // 60
                eta_secs = eta_seconds % 60
                parts.append(f"eta={eta_mins}m{eta_secs}s")
        
        msg = " | ".join(parts)
        self.info(f"HEARTBEAT: {msg}")
    
    def log_request_payload(self, endpoint: str, payload: Dict[str, Any]) -> None:
        if not self._verbose:
            return
        
        import json
        try:
            payload_str = json.dumps(payload, indent=2)
            if len(payload_str) > 1000:
                payload_str = payload_str[:1000] + "..."
            self.debug(f"REQUEST_PAYLOAD: {endpoint}\n{payload_str}")
        except Exception:
            self.debug(f"REQUEST_PAYLOAD: {endpoint} | payload={payload}")
    
    def log_response_excerpt(self, endpoint: str, response: str, max_length: int = 500) -> None:
        if not self._verbose:
            return
        
        excerpt = response[:max_length] if len(response) > max_length else response
        self.debug(f"RESPONSE_EXCERPT: {endpoint} | length={len(response)}\n{excerpt}")
    
    def log_timing_breakdown(self, stage: str, breakdown: Dict[str, float]) -> None:
        if not self._verbose:
            return
        
        breakdown_str = ", ".join(f"{k}={v:.1f}ms" for k, v in breakdown.items())
        self.debug(f"TIMING_BREAKDOWN: {stage} | {breakdown_str}")
    
    def log_api_request(self, endpoint: str, **metadata) -> None:
        self.debug(f"API_REQUEST: {endpoint}", extra=metadata)
        if self._verbose and metadata:
            self.debug(f"API_REQUEST_DETAIL: {endpoint} | {metadata}")
    
    def log_api_response(self, endpoint: str, status: int, **metadata) -> None:
        self.debug(f"API_RESPONSE: {endpoint} | status={status}", extra=metadata)
    
    def log_token_progress(self, token_count: int, stage: str) -> None:
        self.debug(f"TOKEN: #{token_count} | stage={stage}")
    
    def _record_timing(self, stage: str, duration_ms: int) -> None:
        if stage not in self._operation_timings:
            self._operation_timings[stage] = []
        self._operation_timings[stage].append(duration_ms)
        
        self._stage_history.append({
            'stage': stage,
            'duration_ms': duration_ms,
            'timestamp': time_module.time()
        })
    
    def get_average_timing(self, stage: str) -> Optional[float]:
        timings = self._operation_timings.get(stage)
        if timings and len(timings) >= 2:
            return sum(timings) / len(timings)
        return None
    
    def get_historical_timings(self, stage: str, limit: int = 10) -> List[float]:
        timings = []
        for entry in reversed(self._stage_history):
            if entry['stage'] == stage:
                timings.append(entry['duration_ms'])
                if len(timings) >= limit:
                    break
        return timings


_logger = VoiceToCodeLogger()


def get_logger() -> VoiceToCodeLogger:
    return _logger
