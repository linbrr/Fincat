import { Bell } from 'lucide-react';

interface Props {
  count: number;
  active: boolean;
  onClick: () => void;
}

export function FeedToggleButton({ count, active, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className={`relative rounded-lg p-1.5 transition-colors ${
        active
          ? 'bg-emerald-50 text-emerald-600'
          : 'text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600'
      }`}
      title={active ? '关闭助手' : '打开助手'}
    >
      <Bell className="h-4 w-4" />
      {count > 0 && (
        <span className="absolute -top-1 -right-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-medium text-white">
          {count > 99 ? '99+' : count}
        </span>
      )}
    </button>
  );
}
