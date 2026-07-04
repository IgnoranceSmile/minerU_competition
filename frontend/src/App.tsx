import { useCallback, useEffect, useState } from 'react';
import Sidebar from './components/Sidebar';
import DrawingViewer from './components/DrawingViewer';
import ChatInput from './components/ChatInput';
import ChatOutput from './components/ChatOutput';
import { chat, getDrawings, uploadZip } from './api';
import type { Drawing, Message } from './types';

let _seq = 0;
const uid = () => `m${++_seq}`;

export default function App() {
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const [selected, setSelected] = useState<Drawing | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);

  useEffect(() => {
    getDrawings()
      .then((d) => { setDrawings(d); setBackendOk(true); })
      .catch(() => setBackendOk(false));
  }, []);

  const handleUpload = useCallback(async (file: File) => {
    const d = await uploadZip(file);
    setDrawings(d);
    setSelected(null);
    setBackendOk(true);
  }, []);

  const handleSelect = useCallback((d: Drawing) => {
    setSelected(d);
  }, []);

  const patchLast = (fn: (m: Message) => Message) =>
    setMessages((ms) => ms.map((m, i) => (i === ms.length - 1 ? fn(m) : m)));

  const handleSend = useCallback(async (text: string) => {
    if (busy || !text.trim()) return;
    setMessages((ms) => [
      ...ms,
      { id: uid(), role: 'user', text },
      { id: uid(), role: 'assistant', text: '', reasoning: '', tools: [], streaming: true },
    ]);
    setBusy(true);
    try {
      for await (const ev of chat(text, selected?.id ?? null, null)) {
        if (ev.type === 'reasoning') {
          patchLast((m) => ({ ...m, reasoning: (m.reasoning || '') + (ev.delta || '') }));
        } else if (ev.type === 'content') {
          patchLast((m) => ({ ...m, text: m.text + (ev.delta || '') }));
        } else if (ev.type === 'tool_start') {
          patchLast((m) => ({ ...m, tools: [...(m.tools || []), { name: ev.name || '' }] }));
        } else if (ev.type === 'progress') {
          // pipeline progress event
        } else if (ev.type === 'tool_result') {
          patchLast((m) => {
            const tools = [...(m.tools || [])];
            for (let i = tools.length - 1; i >= 0; i--) {
              if (tools[i].name === ev.name && !tools[i].result) {
                tools[i] = { ...tools[i], result: ev.result };
                break;
              }
            }
            return { ...m, tools };
          });
        } else if (ev.type === 'done') {
          patchLast((m) => ({ ...m, text: m.text || ev.content || '', streaming: false }));
        }
      }
    } catch (e) {
      patchLast((m) => ({
        ...m, streaming: false,
        text: m.text || `Error: ${(e as Error).message}`,
      }));
    } finally {
      patchLast((m) => ({ ...m, streaming: false }));
      setBusy(false);
    }
  }, [busy, selected]);

  return (
    <div className="app">
      <div className="main">
        <div className="left-mid">
          <div className="brand-bar">
            <div className="brand-row">
              <span className="brand-mark" />
              <span className="brand-name">DrawAgent</span>
              <span className="brand-divider" />
              <span className="brand-sub">工程图纸智能解析 · MinerU-Powered</span>
            </div>
            <div className={`brand-status ${backendOk === true ? 'ok' : ''}`}>
              <span className="status-dot" />
              <span>{backendOk === null ? '连接中...' : backendOk ? '引擎就绪' : '引擎离线'}</span>
            </div>
          </div>
          <div className="left-mid-body">
            <div className="left-body">
              <Sidebar
                drawings={drawings}
                selected={selected}
                onSelect={handleSelect}
                onUpload={handleUpload}
              />
              <ChatInput
                busy={busy}
                onSend={handleSend}
                selected={selected}
              />
            </div>
            <ChatOutput messages={messages} />
          </div>
        </div>
        <DrawingViewer drawing={selected} />
      </div>
    </div>
  );
}
