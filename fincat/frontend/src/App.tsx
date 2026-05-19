import { useCallback, useState } from 'react';
import { DndContext, DragEndEvent, DragStartEvent, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import { useWebSocket } from './hooks/useWebSocket';
import { ChatPanel } from './components/ChatPanel/ChatPanel';
import { FloatingContextLayer } from './components/FloatingContext/FloatingContextLayer';
import { ChatDropZone } from './components/ChatPanel/ChatDropZone';

export default function App() {
  const { messages, topics, connected, loading, model, send, sendWithFiles, stop, sendFeedback } = useWebSocket();
  const [dragging, setDragging] = useState(false);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));

  const handleDragStart = useCallback((_event: DragStartEvent) => {
    setDragging(true);
  }, []);

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    setDragging(false);
    if (event.over?.id === 'chat-drop-zone' && event.active.data.current?.topic) {
      send(event.active.data.current.topic.content);
    }
  }, [send]);

  const handleRetry = useCallback((messageId: string) => {
    const idx = messages.findIndex((m) => m.id === messageId);
    if (idx <= 0) return;
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].role === 'user') {
        send(messages[i].content);
        break;
      }
    }
  }, [messages, send]);

  return (
    <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
      <div className="relative h-screen w-screen overflow-hidden bg-[#f7f7f8] text-zinc-900">
        <ChatPanel messages={messages} connected={connected} loading={loading} model={model} onSend={send} onFiles={sendWithFiles} onStop={stop} onRetry={handleRetry} />
        {dragging && <ChatDropZone />}
        <FloatingContextLayer topics={topics} onSend={send} sendFeedback={sendFeedback} />
      </div>
    </DndContext>
  );
}
