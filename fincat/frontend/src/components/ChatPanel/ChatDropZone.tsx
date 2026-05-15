import { useDroppable } from '@dnd-kit/core';
import { MessageCircle } from 'lucide-react';

export function ChatDropZone() {
  const { setNodeRef, isOver } = useDroppable({ id: 'chat-drop-zone' });

  return (
    <div
      ref={setNodeRef}
      className={`absolute bottom-16 left-1/2 z-50 flex -translate-x-1/2 items-center justify-center gap-3
                  rounded-3xl border-2 border-dashed w-[960px] py-8
                  transition-all duration-200
                  ${isOver
                    ? 'border-emerald-400 bg-emerald-50 shadow-[0_0_32px_rgba(16,185,129,0.2)] scale-[1.03]'
                    : 'border-zinc-300 bg-white/80 shadow-lg backdrop-blur-sm'
                  }`}
    >
      <MessageCircle className={`h-6 w-6 ${isOver ? 'text-emerald-500' : 'text-zinc-400'}`} />
      <span className={`text-base font-medium ${isOver ? 'text-emerald-600' : 'text-zinc-500'}`}>
        {isOver ? '松开发送' : '拖拽到此处发送'}
      </span>
    </div>
  );
}
