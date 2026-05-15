import { useEffect, useRef, useState, useCallback } from 'react';
import type { Message } from '../../types';
import { MessageBubble } from './MessageBubble';

interface Props {
  messages: Message[];
  onRetry?: (messageId: string) => void;
}

export function MessageList({ messages, onRetry }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const userScrolled = useRef(false);

  const scrollToBottom = useCallback((smooth = true) => {
    endRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'instant' });
    userScrolled.current = false;
  }, []);

  useEffect(() => {
    if (!userScrolled.current) {
      scrollToBottom(false);
    }
  }, [messages.length, scrollToBottom]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom > 120) {
      userScrolled.current = true;
      setShowScrollBtn(true);
    } else {
      userScrolled.current = false;
      setShowScrollBtn(false);
    }
  }, []);

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="relative flex-1 overflow-y-auto">
      {messages.length === 0 && (
        <div className="flex h-full flex-col items-center pt-[22vh] gap-8">
          <img src="/LOGO.png" alt="fincat" className="w-[520px] rounded-3xl object-contain" />
          <div className="text-center">
            <p className="text-2xl font-semibold text-zinc-600 tracking-wide">开始对话</p>
            <p className="mt-2 text-lg text-zinc-400">输入消息，开始与 Fincat 交流</p>
          </div>
        </div>
      )}
      <div className="mx-auto max-w-4xl px-6 py-6">
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} onRetry={onRetry} />
        ))}
      </div>
      <div ref={endRef} />

      {showScrollBtn && (
        <button
          onClick={() => scrollToBottom()}
          className="absolute bottom-4 left-1/2 -translate-x-1/2 flex h-8 w-8 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-400 hover:text-gray-600 hover:border-gray-300 shadow-sm transition-all"
          title="滚动到底部"
        >
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      )}
    </div>
  );
}
