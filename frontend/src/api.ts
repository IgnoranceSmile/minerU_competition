// 后端 API 客户端。开发期经 vite 代理 /api → 127.0.0.1:8000
import type { ChatEvent, Drawing, Region } from './types';

const BASE = (import.meta.env.VITE_API_BASE as string) || '';

export async function getDrawings(): Promise<Drawing[]> {
  const r = await fetch(`${BASE}/api/drawings`);
  if (!r.ok) throw new Error('加载图纸库失败');
  const d = await r.json();
  return (d.drawings as Drawing[]) || [];
}

export async function uploadZip(file: File): Promise<Drawing[]> {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`${BASE}/api/upload`, { method: 'POST', body: fd });
  if (!r.ok) throw new Error('上传失败：请确认是 .zip 压缩包');
  const d = await r.json();
  return (d.drawings as Drawing[]) || [];
}

export function drawingPageUrl(id: string, page = 0): string {
  return `${BASE}/api/drawing_page?id=${encodeURIComponent(id)}&page=${page}`;
}

export function imageUrl(path: string): string {
  return path.startsWith('/api') ? `${BASE}${path}` : path;
}

// POST /api/chat，按 SSE 帧逐事件 yield
export async function* chat(
  prompt: string,
  target: string | null,
  region: Region | null,
): AsyncGenerator<ChatEvent> {
  const r = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, target_drawing: target, region }),
  });
  if (!r.ok || !r.body) throw new Error('对话请求失败：后端未就绪');

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // sse-starlette 用 \r\n 作行分隔，统一归一化为 \n 再切帧
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let sep: number;
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('data:')) data += line.slice(5).trimStart();
      }
      if (data) {
        try {
          yield JSON.parse(data) as ChatEvent;
        } catch {
          /* 跳过不完整帧 */
        }
      }
    }
  }
}
