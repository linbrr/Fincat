import type { Prediction } from '../../types';

interface Props {
  predictions: Prediction[];
  onAction: (query: string) => void;
}

export function PredictionCard({ predictions, onAction }: Props) {
  if (!predictions.length) {
    return (
      <div className="flex h-full items-center justify-center text-gray-400 text-sm">
        暂无预测推荐
      </div>
    );
  }

  return (
    <div className="space-y-2 overflow-y-auto p-2">
      {predictions.map((pred, i) => (
        <div
          key={i}
          className="rounded-lg border border-blue-100 bg-blue-50 p-3"
        >
          <div className="mb-1 flex items-center justify-between">
            <span className="text-sm font-medium text-blue-800">
              {pred.title}
            </span>
            {pred.confidence && (
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  pred.confidence === 'high'
                    ? 'bg-green-100 text-green-700'
                    : pred.confidence === 'medium'
                      ? 'bg-yellow-100 text-yellow-700'
                      : 'bg-gray-100 text-gray-500'
                }`}
              >
                {pred.confidence}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-600">{pred.content}</p>
          {pred.actions && pred.actions.length > 0 && (
            <div className="mt-2 flex gap-1.5">
              {pred.actions.map((action, j) => (
                <button
                  key={j}
                  className="rounded bg-blue-600 px-2 py-0.5 text-[11px] text-white hover:bg-blue-700"
                  onClick={() => onAction(action.query)}
                >
                  {action.label}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
