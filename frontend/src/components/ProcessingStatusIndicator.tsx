import { useState, useEffect } from 'react';
import { Brain, Code2, X } from 'lucide-react';

type StatusState = 'thinking' | 'coding' | 'hidden';

interface ProcessingStatusIndicatorProps {
  isVisible: boolean;
  onComplete?: () => void;
  onDismiss?: () => void;
}

const THINKING_DURATION = 2000;
const TOTAL_DURATION = 4000;

export function ProcessingStatusIndicator({
  isVisible,
  onComplete,
  onDismiss
}: ProcessingStatusIndicatorProps) {
  const [status, setStatus] = useState<StatusState>('thinking');

  useEffect(() => {
    if (!isVisible) {
      setStatus('hidden');
      return;
    }

    setStatus('thinking');

    const thinkingTimer = setTimeout(() => {
      setStatus('coding');
    }, THINKING_DURATION);

    const hideTimer = setTimeout(() => {
      setStatus('hidden');
      onComplete?.();
    }, TOTAL_DURATION);

    return () => {
      clearTimeout(thinkingTimer);
      clearTimeout(hideTimer);
    };
  }, [isVisible, onComplete]);

  if (status === 'hidden') return null;

  const isThinking = status === 'thinking';

  return (
    <div className="fixed top-4 right-4 z-50 animate-fade-in">
      <div className="flex items-center gap-3 px-4 py-3 bg-gray-900 rounded-lg shadow-lg border border-gray-700">
        <div className="relative">
          {isThinking ? (
            <Brain className="w-6 h-6 text-purple-400 animate-pulse" />
          ) : (
            <Code2 className="w-6 h-6 text-green-400" />
          )}
          <span className="absolute -bottom-1 -right-1 w-2 h-2 bg-green-500 rounded-full animate-ping" />
        </div>
        
        <div className="flex flex-col">
          <span className="text-sm font-medium text-white">
            {isThinking ? 'Thinking...' : 'Coding...'}
          </span>
          <span className="text-xs text-gray-400">
            {isThinking ? 'Analyzing your request' : 'Generating code'}
          </span>
        </div>

        <button
          onClick={() => {
            setStatus('hidden');
            onDismiss?.();
          }}
          className="ml-2 p-1 hover:bg-gray-700 rounded transition-colors"
          aria-label="Dismiss"
        >
          <X className="w-4 h-4 text-gray-400" />
        </button>
      </div>
    </div>
  );
}

export default ProcessingStatusIndicator;
