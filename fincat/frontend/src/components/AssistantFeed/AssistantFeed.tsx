import { useState } from 'react';
import { X, Bell, Search, CheckCircle, Sparkles, AlertTriangle, ListTodo } from 'lucide-react';
import type { Topic } from '../../types';

interface Props {
  topics: Topic[];
  onClose: () => void;
}

const CATEGORY_CONFIG: Record<string, { label: string; icon: React.ReactNode; order: number }> = {
  alert: { label: '提醒', icon: <AlertTriangle className="h-3.5 w-3.5" />, order: 0 },
  reminder: { label: '待办', icon: <ListTodo className="h-3.5 w-3.5" />, order: 1 },
  news: { label: '资讯', icon: <Search className="h-3.5 w-3.5" />, order: 2 },
  insight: { label: '洞察', icon: <Sparkles className="h-3.5 w-3.5" />, order: 3 },
};

const PRIORITY_COLORS: Record<number, string> = {
  3: 'bg-red-500',
  2: 'bg-amber-500',
  1: 'bg-blue-500',
  0: 'bg-gray-300',
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

function FeedItem({ topic, onDismiss }: { topic: Topic; onDismiss: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="group relative mx-3 cursor-pointer rounded-2xl border border-black/5 bg-white p-4 transition-all hover:bg-zinc-50">
      {/* Priority bar */}
      <div className={`absolute left-0 top-3 bottom-3 w-[3px] rounded-full ${PRIORITY_COLORS[topic.priority] ?? PRIORITY_COLORS[0]}`} />

      <div className="pl-2">
        <p className="text-sm font-medium text-zinc-900 leading-snug">{topic.title}</p>
        <p className={`mt-1 text-sm text-zinc-500 leading-relaxed ${expanded ? '' : 'line-clamp-2'}`}>
          {topic.content}
        </p>
        <div className="mt-2 flex items-center gap-2">
          <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] text-zinc-400">
            {topic.source_name}
          </span>
          <span className="text-[11px] text-zinc-400">{relativeTime(topic.created_at)}</span>
        </div>
      </div>

      {/* Hover actions */}
      <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          onClick={(e) => { e.stopPropagation(); onDismiss(topic.topic_id); }}
          className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
          title="忽略"
        >
          <X className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
          className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
          title={expanded ? '收起' : '详情'}
        >
          <CheckCircle className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

export function AssistantFeed({ topics, onClose }: Props) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const visible = topics.filter((t) => !dismissed.has(t.topic_id));

  const grouped = Object.entries(CATEGORY_CONFIG)
    .map(([key, config]) => ({
      key,
      ...config,
      items: visible.filter((t) => t.category === key),
    }))
    .filter((g) => g.items.length > 0)
    .sort((a, b) => a.order - b.order);

  const handleDismiss = (id: string) => {
    setDismissed((prev) => new Set(prev).add(id));
  };

  return (
    <div className="flex h-full w-[320px] flex-col border-l border-black/5 bg-[#fafafa]">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-black/5 px-5 py-3">
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4 text-zinc-400" />
          <span className="text-sm font-medium text-zinc-900">AI 助手</span>
          {visible.length > 0 && (
            <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600">
              {visible.length}
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto py-3 space-y-4">
        {grouped.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-zinc-400">暂无新动态</p>
          </div>
        ) : (
          grouped.map((group) => (
            <div key={group.key}>
              <div className="flex items-center gap-1.5 px-5 py-1.5 text-xs font-medium uppercase tracking-wide text-zinc-400">
                {group.icon}
                {group.label}
              </div>
              <div className="space-y-2">
                {group.items.map((topic) => (
                  <FeedItem key={topic.topic_id} topic={topic} onDismiss={handleDismiss} />
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
