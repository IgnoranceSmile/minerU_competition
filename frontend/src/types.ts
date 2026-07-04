// 与后端 contracts/interfaces.py 对齐的前端类型

export interface Drawing {
  id: string;
  name: string;
  discipline: string;
  page_count: number;
}

export type RegionKind = 'point' | 'box';

export interface Region {
  kind: RegionKind;
  drawing_id: string;
  page: number;
  x?: number;
  y?: number;
  bbox?: [number, number, number, number];
}

export interface Source {
  drawing: string;
  bbox?: unknown;
  note?: string;
}

export type AnswerType = 'text' | 'table' | 'bbox_image' | 'markdown_list' | 'json_data' | 'file_export';

export interface TaskResult {
  answer_type: AnswerType;
  content: string;
  evidence: Source[];
  extra_images: string[];
  ok: boolean;
  error: string;
}

// SSE 事件（见 src/harness/agent.py 事件契约）
export interface ChatEvent {
  type: 'reasoning' | 'content' | 'tool_start' | 'tool_result' | 'progress' | 'done';
  delta?: string;
  name?: string;
  result?: TaskResult;
  content?: string;
  step?: number;
  max_steps?: number;
  pipeline?: string;
}

// 一次工具调用在 UI 中的呈现
export interface PipelineRun {
  name: string;
  result?: TaskResult;
}

// 对话消息
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  reasoning?: string;
  tools?: PipelineRun[];
  region?: RegionKind;
  streaming?: boolean;
}
