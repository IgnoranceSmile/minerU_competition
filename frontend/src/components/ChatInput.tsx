import { useState } from 'react';
import type { Drawing } from '../types';

const SAMPLES = [
  '哪些图纸解析结果为空或内容很少？',
  '提取结构设计说明中所有表格',
  '建筑和结构的设计号是否一致？',
];

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
  selected: Drawing | null;
}

export default function ChatInput({ busy, onSend, selected }: Props) {
  const [draft, setDraft] = useState('');

  const send = (t?: string) => {
    const text = t || draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft('');
  };

  return (
    <div className="chat-input-area">
      <div className="panel-head">
        <span>对话</span>
        <span className="head-tag">DeepSeek-Chat + MinerU</span>
      </div>
      <div className="input-content">
        <p className="ci-hint">
          向 DrawAgent 提问，调度 5 个 Pipeline 完成图纸问答、表格提取、跨图比对与质量验证。
        </p>
        <div className="ci-samples">
          {SAMPLES.map((s) => (
            <button key={s} className="ci-sample" onClick={() => send(s)}>
              {s}
            </button>
          ))}
        </div>
        <div className="ctx-chips">
          <span className={`ctx-chip ${selected ? 'on' : ''}`}>
            图纸　{selected ? selected.name : '未选择'}
          </span>
        </div>
        <div className="input-row">
          <textarea
            value={draft}
            placeholder="输入问题，Enter 发送"
            rows={2}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button className="btn-send" disabled={busy || !draft.trim()} onClick={() => send()}>
            {busy ? '⟳' : '发送'}
          </button>
        </div>
      </div>
    </div>
  );
}
