/** fincat frontend type definitions */

export interface ToolCall {
  name: string;
  status: 'running' | 'success' | 'error';
}

export interface TableData {
  headers: string[];
  rows: string[][];
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  reasoning?: string[];
  toolCalls?: ToolCall[];
  chartOption?: Record<string, unknown>;
  tableData?: TableData;
}

export interface ChartData {
  type: 'kline' | 'quote' | 'financial' | 'hsgt' | 'block' | 'news' | 'prediction';
  payload: Record<string, unknown>;
  timestamp?: string;
}

export interface Prediction {
  title: string;
  content: string;
  actions?: PredictionAction[];
  confidence?: 'high' | 'medium' | 'low';
}

export interface PredictionAction {
  label: string;
  query: string;
}

export interface KlineData {
  dates: string[];
  ohlc: [number, number, number, number][];  // [open, close, low, high]
  volumes: number[];
  name: string;
  code: string;
}

export interface QuoteRow {
  code: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  turnover: number;
}

export interface Topic {
  topic_id: string;
  source: string;
  source_name: string;
  category: 'alert' | 'news' | 'reminder' | 'insight';
  title: string;
  content: string;
  priority: number;
  created_at: string;
}
