import { useState, type MouseEvent } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ReactECharts from 'echarts-for-react';
import { Brain, Wrench, ChartLine, Table2 } from 'lucide-react';
import type { Message } from '../../types';
import { ExpandableBlock } from './ExpandableBlock';

interface Props {
  message: Message;
  onRetry?: (messageId: string) => void;
}

function CopyIcon() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function RetryIcon() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

export function MessageBubble({ message, onRetry }: Props) {
  const isUser = message.role === 'user';
  const [copied, setCopied] = useState(false);

  const hasBlocks = !isUser && (
    message.reasoning?.length ||
    message.toolCalls?.length ||
    message.chartOption ||
    message.tableData
  );

  const handleCopy = (e: MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleRetry = (e: MouseEvent) => {
    e.stopPropagation();
    onRetry?.(message.id);
  };

  return (
    <div className={`group flex gap-3 py-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-sm font-semibold mt-0.5 ${
        isUser
          ? 'bg-gray-600 text-white'
          : 'bg-gray-600 text-white'
      }`}>
        {isUser ? 'U' : 'AI'}
      </div>

      {/* Content */}
      <div className={`flex max-w-[75%] flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        {/* Main bubble */}
        <div
          className="rounded-2xl px-5 py-4 text-[15px] leading-[1.75] bg-white text-gray-800 shadow-sm border border-gray-200"
        >
          {isUser ? (
            <span className="whitespace-pre-wrap">{message.content}</span>
          ) : message.streaming ? (
            <span className="streaming-cursor whitespace-pre-wrap">{message.content}</span>
          ) : (
            <div className="md-content">
              <Markdown remarkPlugins={[remarkGfm]}>{message.content}</Markdown>
            </div>
          )}
        </div>

        {/* Expandable blocks — only for assistant messages */}
        {!isUser && !message.streaming && hasBlocks && (
          <div className="mt-3 w-full space-y-3">
            {/* Reasoning */}
            {message.reasoning && message.reasoning.length > 0 && (
              <ExpandableBlock title="推理过程" icon={<Brain className="h-4 w-4" />}>
                <div className="space-y-2 text-sm leading-7 text-gray-600">
                  {message.reasoning.map((step, i) => (
                    <p key={i}>{step}</p>
                  ))}
                </div>
              </ExpandableBlock>
            )}

            {/* Tool Calls */}
            {message.toolCalls && message.toolCalls.length > 0 && (
              <ExpandableBlock title="工具调用" icon={<Wrench className="h-4 w-4" />}>
                <div className="space-y-2">
                  {message.toolCalls.map((tool, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between rounded-xl border border-gray-100 bg-gray-50 px-4 py-3"
                    >
                      <span className="text-sm text-gray-700">{tool.name}</span>
                      <span className={`rounded-full px-2 py-1 text-xs ${
                        tool.status === 'success'
                          ? 'bg-emerald-50 text-emerald-600'
                          : tool.status === 'error'
                            ? 'bg-red-50 text-red-600'
                            : 'bg-amber-50 text-amber-600'
                      }`}>
                        {tool.status === 'success' ? '完成' : tool.status === 'error' ? '失败' : '运行中'}
                      </span>
                    </div>
                  ))}
                </div>
              </ExpandableBlock>
            )}

            {/* Chart */}
            {message.chartOption && (
              <ExpandableBlock title="趋势分析" icon={<ChartLine className="h-4 w-4" />}>
                <div className="rounded-2xl border border-gray-100 bg-white p-3">
                  <ReactECharts
                    option={message.chartOption}
                    style={{ height: 300 }}
                    opts={{ renderer: 'svg' }}
                  />
                </div>
              </ExpandableBlock>
            )}

            {/* Table */}
            {message.tableData && (
              <ExpandableBlock title="数据表格" icon={<Table2 className="h-4 w-4" />}>
                <div className="overflow-hidden rounded-2xl border border-gray-200">
                  <table className="w-full">
                    <thead className="bg-gray-50">
                      <tr className="border-b border-gray-200">
                        {message.tableData.headers.map((h, i) => (
                          <th key={i} className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {message.tableData.rows.map((row, ri) => (
                        <tr key={ri} className="border-b border-gray-100 transition-all hover:bg-gray-50">
                          {row.map((cell, ci) => (
                            <td key={ci} className={`px-4 py-4 text-sm ${
                              cell.startsWith('+') ? 'text-emerald-600 font-medium' :
                              cell.startsWith('-') ? 'text-red-500 font-medium' :
                              'text-gray-600'
                            }`}>
                              {cell}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </ExpandableBlock>
            )}
          </div>
        )}

        {/* Action bar */}
        {!isUser && !message.streaming && (
          <div className="mt-1.5 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
            <button
              onClick={handleCopy}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition-colors"
              title="复制"
            >
              {copied ? <CheckIcon /> : <CopyIcon />}
            </button>
            {onRetry && (
              <button
                onClick={handleRetry}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition-colors"
                title="重新生成"
              >
                <RetryIcon />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
