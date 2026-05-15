import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

interface Props {
  title: string;
  icon: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function ExpandableBlock({ title, icon, defaultOpen = false, children }: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-2xl border border-gray-100 bg-white overflow-hidden shadow-md">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-all hover:bg-gray-50"
      >
        <div className="flex items-center gap-3">
          <div className="text-gray-400">{icon}</div>
          <span className="text-sm font-medium text-gray-700">{title}</span>
        </div>
        {open ? (
          <ChevronDown className="h-4 w-4 text-gray-400" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-400" />
        )}
      </button>
      {open && (
        <div className="border-t border-gray-100 p-4">
          {children}
        </div>
      )}
    </div>
  );
}
