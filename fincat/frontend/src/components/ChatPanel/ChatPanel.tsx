import { Bot } from 'lucide-react';
import type { Message } from '../../types';
import { MessageList } from './MessageList';
import { InputBar } from './InputBar';

interface Props {
  messages: Message[];
  connected: boolean;
  loading: boolean;
  model: string;
  onSend: (text: string) => void;
  onStop: () => void;
  onRetry?: (messageId: string) => void;
}

export function ChatPanel({ messages, connected, loading, model, onSend, onStop, onRetry }: Props) {
  return (
    <div className="flex h-full w-full flex-1 flex-col bg-[#f7f7f8]">
      {/* Header — soft atmospheric layer */}
      <header className="flex items-center justify-between px-8 py-5 bg-[#f7f7f8]">
        {/* Left — Workspace Identity */}
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white shadow-sm ring-1 ring-black/5">
            <img src="/fincat.png" alt="fincat" className="h-7 w-7 rounded-lg object-contain" />
          </div>
          <div className="flex flex-col justify-center">
            <span className="text-[18px] font-semibold tracking-tight text-zinc-900">Fincat</span>
            <span className="text-[13px] text-zinc-500">AI Financial Workspace</span>
          </div>
        </div>

        {/* Right — AI Status */}
        <div className="flex items-center gap-3">
          {model && (
            <span className="rounded-full bg-zinc-100 px-3 py-1.5 text-[12px] font-medium text-zinc-500 ring-1 ring-black/5">
              {model}
            </span>
          )}
          {loading && (
            <span className="flex items-center gap-2 rounded-full bg-amber-50 px-4 py-1.5 text-[13px] font-medium text-amber-600 ring-1 ring-amber-200/50">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
              思考中
            </span>
          )}
          <div className="flex items-center gap-2.5 rounded-full bg-white/80 px-4 py-2 shadow-sm ring-1 ring-black/5">
            <div className={`h-2 w-2 rounded-full ${connected ? 'bg-emerald-500' : 'bg-zinc-300'}`} />
            <span className="text-[13px] font-medium text-zinc-700">
              {connected ? '在线' : '离线'}
            </span>
          </div>
        </div>
      </header>

      {/* Messages */}
      <MessageList messages={messages} onRetry={onRetry} />

      {/* Input */}
      <InputBar onSend={onSend} loading={loading} onStop={onStop} empty={messages.length === 0} />
    </div>
  );
}
