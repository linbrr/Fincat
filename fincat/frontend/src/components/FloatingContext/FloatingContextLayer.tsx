import { useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import { ContextCard } from './ContextCard';
import type { Topic } from '../../types';

interface Props {
  topics: Topic[];
  onSend: (text: string) => void;
  sendFeedback?: (topicId: string, feedback: 'dismiss' | 'click' | 'reject') => void;
}

export function FloatingContextLayer({ topics, onSend, sendFeedback }: Props) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const visible = topics.filter((t) => !dismissed.has(t.topic_id));

  if (visible.length === 0) return null;

  return (
    <div className="pointer-events-none absolute inset-y-0 right-0 z-30 flex w-[440px] items-center justify-end p-5">
      <div className="pointer-events-auto flex flex-col gap-4">
        <AnimatePresence mode="popLayout">
          {visible.slice(0, 3).map((topic, i) => (
            <div
              key={topic.topic_id}
              style={{ transform: `translateX(${i % 2 === 0 ? 0 : 12}px)` }}
            >
              <ContextCard
                topic={topic}
                index={i}
                onDismiss={() => {
                  setDismissed((prev) => new Set(prev).add(topic.topic_id));
                  sendFeedback?.(topic.topic_id, 'dismiss');
                }}
                onAskAI={() => {
                  sendFeedback?.(topic.topic_id, 'click');
                  onSend(topic.content);
                }}
              />
            </div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
