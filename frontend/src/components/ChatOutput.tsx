import { useEffect, useRef, useState } from 'react';
import { marked } from 'marked';
import { imageUrl } from '../api';
import type { Message, TaskResult, PipelineRun } from '../types';

const PIPELINE_LABELS: Record<string, string> = {
  p1_drawing_qa: 'P1 · 图纸问答',
  p2_table_extract: 'P2 · 表格提取',
  p3_batch_parse: 'P3 · 批量统计',
  p4_cross_drawing: 'P4 · 跨图比对',
  p5_quality_verify: 'P5 · 质量验证',
};

function md(text: string): string {
  return marked.parse(text || '', { async: false }) as string;
}

function ResultCard({ r }: { r: TaskResult }) {
  if (!r.ok) {
    return <div className="result-card err"><p>⚠ {r.error || '执行失败'}</p></div>;
  }
  return (
    <div className="result-card">
      {r.content && (
        <div className="result-md" dangerouslySetInnerHTML={{ __html: md(r.content) }} />
      )}
      {r.extra_images?.map((src, i) => (
        <img key={i} className="result-img" src={imageUrl(src)} alt="标注图" />
      ))}
      {r.evidence?.length > 0 && (
        <div className="result-ev">
          来源　{r.evidence.map((e) => e.drawing).join('　·　')}
        </div>
      )}
    </div>
  );
}

function PipelineBlock({ t }: { t: PipelineRun }) {
  return (
    <div className="tool-run">
      <div className={`tool-chip ${t.result ? 'done' : 'running'}`}>
        <span className="tool-dot" />
        <span>{PIPELINE_LABELS[t.name] || t.name}</span>
        {!t.result && <span className="tool-running">运行中...</span>}
      </div>
      {t.result && <ResultCard r={t.result} />}
    </div>
  );
}

function Reasoning({ text }: { text: string }) {
  const [open, setOpen] = useState(true);
  if (!text) return null;
  return (
    <div className={`reasoning ${open ? 'open' : ''}`}>
      <button className="reasoning-head" onClick={() => setOpen((o) => !o)}>
        <span className="rh-icon">✓</span>
        <span>思考过程</span>
        <span className="rh-toggle">{open ? '收起' : '展开'}</span>
      </button>
      {open && <div className="reasoning-body">{text}</div>}
    </div>
  );
}

function Bubble({ m }: { m: Message }) {
  if (m.role === 'user') {
    return (
      <div className="msg msg-user">
        <div className="bubble">
          <p>{m.text}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="msg msg-bot">
      <div className="bot-avatar" />
      <div className="bot-body">
        <Reasoning text={m.reasoning || ''} />
        {m.tools?.map((t, i) => <PipelineBlock key={i} t={t} />)}
        {m.text && (
          <div className="answer" dangerouslySetInnerHTML={{ __html: md(m.text) }} />
        )}
        {m.streaming && !m.text && (m.tools?.length ?? 0) === 0 && !m.reasoning && (
          <div className="thinking-dots"><span /><span /><span /></div>
        )}
      </div>
    </div>
  );
}

interface Props {
  messages: Message[];
}

export default function ChatOutput({ messages }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  return (
    <section className="chat-output">
      <div className="panel-head">
        <span>对话结果</span>
        {messages.length > 0 && (
          <span className="head-tag">{messages.length} 条</span>
        )}
      </div>
      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="output-empty">
            <p>等待提问...</p>
          </div>
        )}
        {messages.map((m) => <Bubble key={m.id} m={m} />)}
      </div>
    </section>
  );
}
