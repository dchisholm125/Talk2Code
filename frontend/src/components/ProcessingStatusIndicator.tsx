import { useState, useEffect, useCallback, useRef } from 'react';
import { Brain, Code2, X, Clock, Zap } from 'lucide-react';

type StatusState = 'idle' | 'thinking' | 'coding' | 'complete' | 'hidden';

interface ProgressData {
  stage: string;
  progress: number;
  elapsed_s: number;
  eta_seconds?: number;
  tokens?: number;
  message?: string;
  visual_indicators?: VisualIndicatorState;
  session_id?: number;
}

interface VisualIndicatorState {
  thinking: boolean;
  coding: boolean;
}

interface SessionCircle {
  name: string;
  files: string[];
  reason: string;
}

interface SessionEnvelope {
  intent_summary?: string;
  summary_text?: string;
  working_set?: string[];
  circles?: SessionCircle[];
  git_history?: string;
}

interface SessionDetailsResponse {
  session_id: number;
  state?: {
    context_envelope?: SessionEnvelope;
  };
  events?: Array<{
    event_type: string;
    payload?: Record<string, unknown>;
    reason?: string;
  }>;
}

const THINKING_STAGE_KEYS = new Set(['compressing', 'invoking_assistant', 'thinking']);
const CODING_STAGE_KEYS = new Set(['writing', 'tool_execution', 'executing_code', 'summarizing', 'executing']);

interface ProcessingStatusIndicatorProps {
  isVisible: boolean;
  progressEndpoint?: string;
  onComplete?: () => void;
  onDismiss?: () => void;
}

export function ProcessingStatusIndicator({
  isVisible,
  progressEndpoint = '/observability/progress',
  onComplete,
  onDismiss
}: ProcessingStatusIndicatorProps) {
  const [status, setStatus] = useState<StatusState>('idle');
  const [progress, setProgress] = useState<ProgressData | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [visualIndicators, setVisualIndicators] = useState<VisualIndicatorState>({
    thinking: false,
    coding: false,
  });
  const eventSourceRef = useRef<EventSource | null>(null);
  const [sessionEnvelope, setSessionEnvelope] = useState<SessionEnvelope | null>(null);
  const [sessionEventCount, setSessionEventCount] = useState<number | null>(null);

  const connectToProgressStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const eventSource = new EventSource(progressEndpoint);
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      setIsConnected(true);
    };

    eventSource.onmessage = (event) => {
      try {
        const data: ProgressData = JSON.parse(event.data);
        setProgress(data);
        const nextIndicators: VisualIndicatorState = {
          thinking: data.visual_indicators?.thinking ?? false,
          coding: data.visual_indicators?.coding ?? false,
        };
        setVisualIndicators(nextIndicators);

        const normalizedStage = (data.stage || '').toLowerCase();
        if (normalizedStage === 'complete') {
          setStatus('complete');
        } else if (THINKING_STAGE_KEYS.has(normalizedStage)) {
          setStatus('thinking');
        } else if (CODING_STAGE_KEYS.has(normalizedStage)) {
          setStatus('coding');
        } else if (nextIndicators.coding) {
          setStatus('coding');
        } else if (nextIndicators.thinking) {
          setStatus('thinking');
        }
      } catch (error) {
        console.error('Failed to parse progress data:', error);
      }
    };

    eventSource.onerror = () => {
      setIsConnected(false);
      setVisualIndicators({ thinking: false, coding: false });
      eventSource.close();
    };

    return () => {
      eventSource.close();
      setIsConnected(false);
    };
  }, [progressEndpoint]);

  useEffect(() => {
    if (!isVisible) {
      setStatus('hidden');
      setVisualIndicators({ thinking: false, coding: false });
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      return;
    }

    setStatus('thinking');
    connectToProgressStream();

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [isVisible, connectToProgressStream]);

  useEffect(() => {
    if (status === 'complete' && onComplete) {
      const timer = setTimeout(() => {
        onComplete();
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [status, onComplete]);

  const buildSessionUrl = (sessionId: number): string => {
    const base = progressEndpoint.replace(/\/progress\/?$/, '') || '/observability';
    return `${base}/sessions/${sessionId}`;
  };

  useEffect(() => {
    const sessionId = progress?.session_id;
    if (!sessionId) {
      setSessionEnvelope(null);
      setSessionEventCount(null);
      return;
    }

    const controller = new AbortController();
    let active = true;
    const sessionUrl = buildSessionUrl(sessionId);

    fetch(sessionUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error('Session context unavailable');
        }
        return response.json() as Promise<SessionDetailsResponse>;
      })
      .then((payload) => {
        if (!active) return;
        const envelope = payload.state?.context_envelope;
        setSessionEnvelope(envelope ?? null);
        setSessionEventCount(payload.events?.length ?? null);
      })
      .catch((error) => {
        console.debug('[SessionContext] fetch failed', error);
        if (active) {
          setSessionEnvelope(null);
          setSessionEventCount(null);
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [progress?.session_id, progressEndpoint]);

  const formatTime = (seconds: number): string => {
    if (seconds < 60) {
      return `${seconds}s`;
    }
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs}s`;
  };

  const getStatusText = (): string => {
    if (!progress) {
      return 'Starting...';
    }

    const stageNames: Record<string, string> = {
      'compressing': 'Compressing conversation...',
      'invoking_assistant': 'Starting assistant...',
      'thinking': 'Analyzing your request',
      'writing': 'Writing response',
      'tool_execution': 'Executing tools',
      'executing_code': 'Running code',
      'executing': 'Finalizing execution',
      'summarizing': 'Generating summary',
      'complete': 'Complete!'
    };

    return stageNames[progress.stage] || progress.stage;
  };

  const getProgressPercent = (): string => {
    if (!progress) {
      return '0%';
    }
    return `${Math.round(progress.progress * 100)}%`;
  };

  const getIndicatorLabel = (): string => {
    if (visualIndicators.thinking && visualIndicators.coding) {
      return 'Thinking + Coding';
    }
    if (visualIndicators.coding) {
      return 'Coding';
    }
    if (visualIndicators.thinking) {
      return 'Thinking';
    }
    return 'Idle';
  };

  const progressBarClass = visualIndicators.coding
    ? 'bg-green-500'
    : visualIndicators.thinking
    ? 'bg-purple-500'
    : 'bg-blue-500';

  if (status === 'hidden') return null;

  const isThinking = status === 'thinking';
  const isCoding = status === 'coding';
  const isComplete = status === 'complete';
  const progressPercent = progress?.progress ?? 0;
  const primaryLabel = isComplete
    ? 'Complete!'
    : isThinking
    ? 'Thinking...'
    : isCoding
    ? 'Coding...'
    : 'Waiting...';

  const contextSummary = sessionEnvelope?.summary_text || sessionEnvelope?.intent_summary;
  const workingSet = sessionEnvelope?.working_set ?? [];
  const visibleWorking = workingSet.slice(0, 3);
  const extraWorking = Math.max(0, workingSet.length - visibleWorking.length);

  return (
    <div className="fixed top-4 right-4 z-50 animate-fade-in">
      <div className="flex items-center gap-3 px-4 py-3 bg-gray-900 rounded-lg shadow-lg border border-gray-700 min-w-[280px]">
        <div className="flex flex-col items-center gap-1">
          <div className="flex items-center gap-2">
            <span
              className={`p-2 border rounded-full transition-colors ${
                visualIndicators.thinking
                  ? 'border-purple-500 bg-purple-500/20 text-purple-400'
                  : 'border-gray-700 bg-gray-800 text-gray-500'
              }`}
            >
              <Brain className="w-6 h-6" />
            </span>
            <span
              className={`p-2 border rounded-full transition-colors ${
                visualIndicators.coding
                  ? 'border-emerald-500 bg-emerald-500/20 text-emerald-400'
                  : 'border-gray-700 bg-gray-800 text-gray-500'
              }`}
            >
              <Code2 className="w-6 h-6" />
            </span>
          </div>
          <span className="text-[10px] uppercase tracking-wider text-gray-400">
            {getIndicatorLabel()}
          </span>
        </div>
        
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm font-medium text-white truncate">
              {primaryLabel}
            </span>
            <span className="text-xs text-gray-400 ml-2">
              {getProgressPercent()}
            </span>
          </div>
          
          <div className="w-full h-1.5 bg-gray-700 rounded-full overflow-hidden mb-1">
            <div
              className={`h-full rounded-full transition-all duration-500 ${progressBarClass}`}
              style={{ width: `${progressPercent * 100}%` }}
            />
          </div>
          
          <div className="flex items-center justify-between text-xs text-gray-400">
            <span className="truncate">{getStatusText()}</span>
            
            <div className="flex items-center gap-2 ml-2">
              {progress?.elapsed_s !== undefined && (
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatTime(progress.elapsed_s)}
                </span>
              )}
              {progress?.eta_seconds !== undefined && progress.eta_seconds > 0 && (
                <span className="flex items-center gap-1 text-green-400">
                  <Zap className="w-3 h-3" />
                  ETA: {formatTime(progress.eta_seconds)}
                </span>
              )}
            </div>
          </div>
        </div>

        <button
          onClick={() => {
            setStatus('hidden');
            setVisualIndicators({ thinking: false, coding: false });
            if (eventSourceRef.current) {
              eventSourceRef.current.close();
            }
            onDismiss?.();
          }}
          className="ml-2 p-1 hover:bg-gray-700 rounded transition-colors"
          aria-label="Dismiss"
        >
          <X className="w-4 h-4 text-gray-400" />
        </button>
      </div>
      
      {isConnected && progress && (
        <div className="mt-2 px-3 py-2 bg-gray-800/80 rounded text-xs text-gray-400">
          {progress.message || `Processing: ${progress.stage}`}
        </div>
      )}
      {sessionEnvelope && (
        <div className="mt-2 px-3 py-2 bg-gradient-to-br from-gray-900/70 to-gray-900/10 rounded border border-gray-700 text-[11px] text-gray-200 space-y-2">
          <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-gray-500">
            <span>Session context</span>
            <span>
              {sessionEventCount !== null ? `${sessionEventCount} events` : 'Awaiting log'}
            </span>
          </div>
          <p className="leading-snug text-[11px] text-gray-200">
            {contextSummary || 'Context discovery is warming up.'}
          </p>
          {workingSet.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {visibleWorking.map((file) => (
                <span
                  key={file}
                  className="px-2 py-0.5 bg-gray-800 rounded text-[10px] text-gray-300"
                >
                  {file}
                </span>
              ))}
              {extraWorking > 0 && (
                <span className="px-2 py-0.5 bg-gray-800 rounded text-[10px] text-gray-500">
                  +{extraWorking} more
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default ProcessingStatusIndicator;
