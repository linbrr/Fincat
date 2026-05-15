import { useState } from 'react';
import { motion } from 'framer-motion';
import { useDraggable } from '@dnd-kit/core';
import { X, Search, Sparkles, AlertTriangle, ListTodo, Pin, MessageCircle, GripVertical } from 'lucide-react';
import type { Topic } from '../../types';

const CATEGORY_ICON: Record<string, React.ReactNode> = {
  alert: <AlertTriangle className="h-4 w-4 text-red-400" />,
  reminder: <ListTodo className="h-4 w-4 text-blue-400" />,
  news: <Search className="h-4 w-4 text-zinc-400" />,
  insight: <Sparkles className="h-4 w-4 text-amber-400" />,
};

const PRIORITY_STRIPE: Record<number, string> = {
  3: 'bg-red-400',
  2: 'bg-amber-400',
  1: 'bg-blue-400',
  0: 'bg-zinc-200',
};

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins}分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  return `${days}天前`;
}

interface Props {
  topic: Topic;
  index: number;
  onDismiss: () => void;
  onAskAI: () => void;
}

export function ContextCard({ topic, index, onDismiss, onAskAI }: Props) {
  const [pinned, setPinned] = useState(false);

  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: topic.topic_id,
    data: { topic },
  });

  const dragStyle = transform
    ? { transform: `translate(${transform.x}px, ${transform.y}px)` }
    : undefined;

  return (
    <div
      ref={setNodeRef}
      style={dragStyle}
      className={isDragging ? 'z-50' : ''}
      {...attributes}
    >
      <motion.div
        initial={{ opacity: 0, x: 24, scale: 0.96 }}
        animate={{ opacity: 1, x: 0, scale: 1 }}
        exit={{ opacity: 0, x: 24, scale: 0.96 }}
        transition={{ type: 'spring', stiffness: 280, damping: 24, delay: index * 0.06 }}
        className={`group relative min-h-[160px] select-text overflow-hidden rounded-3xl
                    border border-black/5 bg-white/90
                    shadow-[0_2px_16px_rgba(0,0,0,0.05)]
                    ${isDragging ? 'shadow-[0_8px_32px_rgba(0,0,0,0.12)] scale-[1.02]' : 'backdrop-blur-sm transition-shadow hover:shadow-[0_4px_24px_rgba(0,0,0,0.08)]'}`}
      >
      {/* Priority stripe */}
      <div className={`absolute left-0 top-0 h-full w-[3px] rounded-full ${PRIORITY_STRIPE[topic.priority] ?? PRIORITY_STRIPE[0]}`} />

      <div className="flex h-full flex-col p-5 pl-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            {/* Drag handle */}
            <div
              {...listeners}
              className="flex h-6 w-6 shrink-0 cursor-grab items-center justify-center rounded-full text-zinc-300 hover:text-zinc-500 active:cursor-grabbing"
              title="拖拽发送"
            >
              <GripVertical className="h-3.5 w-3.5" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                {CATEGORY_ICON[topic.category]}
                <p className="truncate text-base font-medium leading-7 text-zinc-800">
                  {topic.title}
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Content */}
        <p className="mt-2 flex-1 text-sm leading-7 text-zinc-500">
          {topic.content}
        </p>

        {/* Meta + actions */}
        <div className="mt-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-zinc-50 px-2 py-0.5 text-[11px] text-zinc-400">
              {topic.source_name}
            </span>
            <span className="text-[11px] text-zinc-300">
              {relativeTime(topic.created_at)}
            </span>
          </div>

          {/* Hover actions */}
          <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
            <button
              onClick={(e) => { e.stopPropagation(); setPinned(!pinned); }}
              className={`rounded-lg p-1.5 transition-colors ${
                pinned ? 'bg-blue-50 text-blue-500' : 'text-zinc-300 hover:bg-zinc-50 hover:text-zinc-500'
              }`}
              title="固定"
            >
              <Pin className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onAskAI(); }}
              className="rounded-lg p-1.5 text-zinc-300 transition-colors hover:bg-emerald-50 hover:text-emerald-500"
              title="问 AI"
            >
              <MessageCircle className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onDismiss(); }}
              className="rounded-lg p-1.5 text-zinc-300 transition-colors hover:bg-red-50 hover:text-red-400"
              title="忽略"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </motion.div>
    </div>
  );
}
