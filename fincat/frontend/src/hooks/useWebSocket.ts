import { useState, useEffect, useRef, useCallback } from 'react';
import type { Message, ToolCall, TableData, Topic } from '../types';
import { buildAutoOption } from '../lib/chartConverter';

const WS_URL = `ws://${window.location.hostname}:8765`;

export function useWebSocket() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState('');
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(`${WS_URL}/?client_id=web-${Date.now()}`);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        setTimeout(connect, 3000);
      };
      ws.onerror = () => {};

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);

          if (data.event === 'delta') {
            setLoading(false);
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant' && last.id === data.stream_id) {
                return [...prev.slice(0, -1), { ...last, content: last.content + data.text, streaming: true }];
              }
              return [...prev, { id: data.stream_id, role: 'assistant', content: data.text, streaming: true }];
            });
          }

          if (data.event === 'stream_end') {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.streaming) {
                return [...prev.slice(0, -1), { ...last, streaming: false }];
              }
              return prev;
            });
          }

          if (data.event === 'message') {
            setLoading(false);
            setMessages((prev) => {
              const updated = prev.map((m) => m.streaming ? { ...m, streaming: false } : m);
              const exists = updated.some((m) => m.role === 'assistant' && m.content === data.text);
              if (exists) return updated;
              return [...updated, {
                id: Date.now().toString(),
                role: 'assistant',
                content: data.text,
                reasoning: data.reasoning,
                toolCalls: data.tool_calls,
                chartOption: data.chart_option,
                tableData: data.table_data,
              }];
            });
          }

          if (data.event === 'reasoning') {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                const steps = [...(last.reasoning ?? []), data.text];
                return [...prev.slice(0, -1), { ...last, reasoning: steps }];
              }
              return prev;
            });
          }

          if (data.event === 'tool_call') {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                const calls = [...(last.toolCalls ?? [])];
                const existing = calls.findIndex((c) => c.name === data.name);
                const call: ToolCall = { name: data.name, status: data.status };
                if (existing >= 0) {
                  calls[existing] = call;
                } else {
                  calls.push(call);
                }
                return [...prev.slice(0, -1), { ...last, toolCalls: calls }];
              }
              return prev;
            });
          }

          if (data.event === 'chart') {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                return [...prev.slice(0, -1), { ...last, chartOption: data.option }];
              }
              return prev;
            });
          }

          if (data.event === 'table') {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                const td: TableData = { headers: data.headers, rows: data.rows };
                return [...prev.slice(0, -1), { ...last, tableData: td }];
              }
              return prev;
            });
          }

          // Backend DataFrame chart data → inline bubble chart/table
          if (data.event === 'data') {
            const payload = data.payload as { data?: Record<string, unknown>[]; meta?: Record<string, unknown> } | undefined;
            const records = payload?.data ?? [];
            const meta = payload?.meta ?? {};

            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (!last || last.role !== 'assistant') return prev;

              const { chartOption, tableData } = buildAutoOption(records, meta);
              const update: Partial<Message> = {};
              if (chartOption) update.chartOption = chartOption;
              if (tableData) update.tableData = tableData;

              if (Object.keys(update).length === 0) return prev;
              return [...prev.slice(0, -1), { ...last, ...update }];
            });
          }

          // Proactive topics from TopicDispatcher
          if (data.event === 'topics') {
            const incoming: Topic[] = data.topics ?? [];
            if (incoming.length > 0) {
              setTopics((prev) => {
                const map = new Map(prev.map((t) => [t.topic_id, t]));
                for (const t of incoming) map.set(t.topic_id, t);
                return [...map.values()].sort((a, b) => b.priority - a.priority);
              });
            }
          }

          // Model info from backend
          if (data.event === 'model_info' && data.model) {
            setModel(data.model);
          }
        } catch {}
      };
    }

    connect();
    return () => wsRef.current?.close();
  }, []);

  const send = useCallback((text: string) => {
    if (!text.trim() || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({ content: text }));
    setMessages((prev) => [...prev, { id: Date.now().toString(), role: 'user', content: text }]);
    setLoading(true);
  }, []);

  const requestTopics = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ action: 'request_topics' }));
  }, []);

  const stop = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ action: 'stop' }));
    setLoading(false);
  }, []);

  const sendFeedback = useCallback((topicId: string, feedback: 'dismiss' | 'click' | 'reject') => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ action: 'topic_feedback', topic_id: topicId, feedback }));
  }, []);

  return { messages, topics, connected, loading, model, send, stop, requestTopics, sendFeedback };
}
