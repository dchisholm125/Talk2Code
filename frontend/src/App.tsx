import { useState } from 'react';
import { ProcessingStatusIndicator } from './components/ProcessingStatusIndicator';

export default function App() {
  const [isProcessing, setIsProcessing] = useState(false);

  const handleStartProcessing = () => {
    setIsProcessing(true);
  };

  const handleComplete = () => {
    console.log('Processing complete!');
    setIsProcessing(false);
  };

  const handleDismiss = () => {
    console.log('Indicator dismissed');
  };

  return (
    <div className="min-h-screen bg-gray-950 text-white p-8">
      <div className="max-w-md mx-auto">
        <h1 className="text-2xl font-bold mb-6">Voice-to-Code Processing</h1>
        
        <button
          onClick={handleStartProcessing}
          disabled={isProcessing}
          className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-600 rounded-lg font-medium transition-colors"
        >
          {isProcessing ? 'Processing...' : 'Start Processing'}
        </button>

        <ProcessingStatusIndicator
          isVisible={isProcessing}
          onComplete={handleComplete}
          onDismiss={handleDismiss}
        />
      </div>
    </div>
  );
}
