import { useState, useEffect } from 'react';
import { KlineChart } from './KlineChart';
import { QuoteTable } from './QuoteTable';
import type { ChartData, KlineData, QuoteRow } from '../../types';

type Tab = 'chart' | 'quote';

interface Props {
  chartData: ChartData | null;
}

/** Transform raw DataFrame records into KlineData for ECharts. */
function toKlineData(records: Record<string, unknown>[], meta: Record<string, unknown>): KlineData | null {
  if (!records.length) return null;

  const get = (row: Record<string, unknown>, ...keys: string[]) => {
    for (const k of keys) {
      if (row[k] !== undefined) return Number(row[k]);
    }
    return 0;
  };

  const dates = records.map((r) => String(r['日期'] ?? r['date'] ?? r['Date'] ?? ''));
  const ohlc: [number, number, number, number][] = records.map((r) => [
    get(r, '开盘', '开', 'open', 'Open'),
    get(r, '收盘', '收', 'close', 'Close'),
    get(r, '最低', '低', 'low', 'Low'),
    get(r, '最高', '高', 'high', 'High'),
  ]);
  const volumes = records.map((r) => get(r, '成交量', '量', 'volume', 'Volume'));

  return {
    dates,
    ohlc,
    volumes,
    name: String(meta.name ?? meta.stock_name ?? ''),
    code: String(meta.code ?? meta.stock_code ?? ''),
  };
}

/** Transform raw DataFrame records into QuoteRow[]. */
function toQuoteRows(records: Record<string, unknown>[]): QuoteRow[] {
  return records.map((r) => ({
    code: String(r['代码'] ?? r['code'] ?? ''),
    name: String(r['名称'] ?? r['name'] ?? ''),
    price: Number(r['最新价'] ?? r['price'] ?? 0),
    change: Number(r['涨跌额'] ?? r['change'] ?? 0),
    changePercent: Number(r['涨跌幅'] ?? r['change_percent'] ?? 0),
    volume: Number(r['成交量'] ?? r['量'] ?? r['volume'] ?? 0),
    turnover: Number(r['成交额'] ?? r['额'] ?? r['turnover'] ?? 0),
  }));
}

export function DataPanel({ chartData }: Props) {
  const [tab, setTab] = useState<Tab>('chart');

  const payload = chartData?.payload as { data?: Record<string, unknown>[]; meta?: Record<string, unknown> } | undefined;
  const records = payload?.data ?? [];
  const meta = payload?.meta ?? {};

  const klineData = chartData?.type === 'kline' ? toKlineData(records, meta) : null;
  const quoteData = chartData?.type === 'quote' ? toQuoteRows(records) : null;

  // Auto-switch tab when new data arrives
  useEffect(() => {
    if (chartData?.type === 'kline') setTab('chart');
    else if (chartData?.type === 'quote') setTab('quote');
  }, [chartData]);

  const tabs: { key: Tab; label: string }[] = [
    { key: 'chart', label: 'K线图' },
    { key: 'quote', label: '行情表' },
  ];

  return (
    <div className="flex h-full flex-col">
      {/* Header tabs */}
      <div className="flex border-b border-slate-800 bg-[#0b1120] px-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            className={`relative px-4 py-2.5 text-xs font-medium transition-colors ${
              tab === t.key
                ? 'text-emerald-400'
                : 'text-slate-500 hover:text-slate-300'
            }`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
            {tab === t.key && (
              <span className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full bg-emerald-500" />
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden bg-[#0a0f1c]">
        {tab === 'chart' && (
          klineData ? (
            <KlineChart data={klineData} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-slate-500">
              <svg className="h-10 w-10 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M3 3v18h18" /><path d="M7 16l4-4 4 4 5-6" />
              </svg>
              <span className="text-xs">询问股票走势后自动显示</span>
            </div>
          )
        )}
        {tab === 'quote' && (
          quoteData?.length ? (
            <QuoteTable data={quoteData} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-slate-500">
              <svg className="h-10 w-10 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M4 6h16M4 10h16M4 14h10M4 18h6" />
              </svg>
              <span className="text-xs">询问行情后自动显示</span>
            </div>
          )
        )}
      </div>
    </div>
  );
}
