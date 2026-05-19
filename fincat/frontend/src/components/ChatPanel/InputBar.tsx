import { useState, useRef, useEffect, type KeyboardEvent, type ChangeEvent } from 'react';
import { Paperclip, X, FileText } from 'lucide-react';

interface Props {
  onSend: (text: string) => void;
  onFiles?: (text: string, files: File[]) => void;
  loading: boolean;
  onStop: () => void;
  empty?: boolean;
}

export function InputBar({ onSend, onFiles, loading, onStop, empty }: Props) {
  const [text, setText] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }, [text]);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  const handleSend = () => {
    const trimmed = text.trim();
    if ((!trimmed && selectedFiles.length === 0) || loading) return;

    if (selectedFiles.length > 0 && onFiles) {
      onFiles(trimmed, selectedFiles);
      setSelectedFiles([]);
      setText('');
    } else if (trimmed) {
      onSend(trimmed);
      setText('');
    }
    ref.current?.focus();
  };

  const handleKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    const pdfs = Array.from(files).filter(f => f.type === 'application/pdf' || f.name.endsWith('.pdf'));
    if (pdfs.length > 0) {
      setSelectedFiles(prev => [...prev, ...pdfs]);
    }
    // Reset input so same file can be selected again
    e.target.value = '';
  };

  const removeFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const canSend = (text.trim().length > 0 || selectedFiles.length > 0) && !loading;

  return (
    <div className={`px-6 ${empty ? 'pb-[20vh]' : 'pb-4'}`}>
      <div className="mx-auto flex max-w-3xl flex-col gap-2 rounded-3xl border border-gray-200 bg-white p-4 shadow-lg transition-all focus-within:border-gray-300 focus-within:shadow-xl">
        {/* Selected files display */}
        {selectedFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 px-2">
            {selectedFiles.map((file, i) => (
              <div key={i} className="flex items-center gap-1.5 rounded-lg bg-gray-100 px-2.5 py-1.5 text-xs text-gray-600">
                <FileText className="h-3.5 w-3.5 text-gray-400" />
                <span className="max-w-[160px] truncate">{file.name}</span>
                <button
                  onClick={() => removeFile(i)}
                  className="ml-0.5 rounded-full p-0.5 hover:bg-gray-200"
                >
                  <X className="h-3 w-3 text-gray-400" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Input row */}
        <div className="flex items-end gap-3">
          {/* Attachment button */}
          <button
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-gray-400 transition-all hover:bg-gray-100 hover:text-gray-600"
            onClick={() => fileRef.current?.click()}
            title="上传 PDF 文件"
          >
            <Paperclip className="h-5 w-5" />
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf"
            multiple
            className="hidden"
            onChange={handleFileChange}
          />

          <textarea
            ref={ref}
            className="flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-[15px] text-gray-800 placeholder:text-gray-400 focus:outline-none leading-relaxed"
            rows={1}
            placeholder={selectedFiles.length > 0 ? "添加说明（可选）..." : "询问市场走势、风险分析、财经新闻..."}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKey}
            disabled={loading}
          />

          {loading ? (
            <button
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-red-500 text-white transition-all hover:bg-red-400 hover:scale-105"
              onClick={onStop}
              title="停止生成"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-all ${
                canSend
                  ? 'bg-gray-600 text-white hover:bg-gray-500 hover:scale-105'
                  : 'bg-gray-100 text-gray-300 cursor-not-allowed'
              }`}
              onClick={handleSend}
              disabled={!canSend}
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          )}
        </div>
      </div>
      <p className="mt-1.5 text-center text-[11px] text-gray-400 select-none">
        Enter 发送 · Shift+Enter 换行 · 附件仅支持 PDF
      </p>
    </div>
  );
}
