/** Convert backend DataFrame records into ECharts option objects. */

const RED = '#ef4444';
const GREEN = '#22c55e';
const LINE_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4'];

function get(row: Record<string, unknown>, ...keys: string[]): number {
  for (const k of keys) {
    if (row[k] !== undefined) return Number(row[k]);
  }
  return 0;
}

function colNames(records: Record<string, unknown>[]): string[] {
  return Object.keys(records[0] || {});
}

function hasAny(row: Record<string, unknown>, ...keys: string[]): boolean {
  return keys.some((k) => row[k] !== undefined);
}

// ── Smart auto-detect: pick the best chart type from data structure ──

export function buildAutoOption(
  records: Record<string, unknown>[],
  meta: Record<string, unknown>,
): { chartOption: Record<string, unknown> | null; tableData: { headers: string[]; rows: string[][] } | null } {
  if (!records.length) return { chartOption: null, tableData: null };

  const first = records[0];
  const cols = colNames(records);

  // Case 1: OHLC data (K线 / candlestick)
  if (hasAny(first, '开盘', '开', 'open', 'Open') && hasAny(first, '收盘', '收', 'close', 'Close')) {
    return { chartOption: buildKlineOption(records, meta), tableData: null };
  }

  // Case 2: Date + multiple numeric columns → trend line chart
  const dateCol = cols.find((c) => hasAny(first, c) && /日期|date/i.test(c));
  if (dateCol && cols.length >= 3) {
    const numericCols = cols.filter((c) => c !== dateCol && !isNaN(Number(first[c])));
    if (numericCols.length >= 1) {
      return { chartOption: buildTrendOption(records, meta), tableData: buildGenericTable(records) };
    }
  }

  // Case 3: Has 涨跌幅 or change_percent → bar chart or table
  if (hasAny(first, '涨跌幅', 'change_percent', '涨跌')) {
    // Multiple rows → bar chart
    if (records.length > 1) {
      return { chartOption: buildQuoteOption(records, meta), tableData: buildQuoteTableData(records) };
    }
    // Single row → table only (a single bar is meaningless)
    return { chartOption: null, tableData: buildQuoteTableData(records) };
  }

  // Case 4: Generic table with numeric data → try trend if date-like first column
  if (cols.length >= 2) {
    const firstCol = cols[0];
    const looksLikeDate = /^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(String(records[0][firstCol] ?? ''));
    if (looksLikeDate) {
      return { chartOption: buildTrendOption(records, meta), tableData: buildGenericTable(records) };
    }
  }

  // Fallback: table only
  return { chartOption: null, tableData: buildGenericTable(records) };
}

// ── Specific builders ──

/** K线图: 蜡烛图 + 成交量 */
export function buildKlineOption(
  records: Record<string, unknown>[],
  meta: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!records.length) return null;

  const dates = records.map((r) => String(r['日期'] ?? r['date'] ?? r['Date'] ?? ''));
  const ohlc: number[][] = records.map((r) => [
    get(r, '开盘', '开', 'open', 'Open'),
    get(r, '收盘', '收', 'close', 'Close'),
    get(r, '最低', '低', 'low', 'Low'),
    get(r, '最高', '高', 'high', 'High'),
  ]);
  const volumes = records.map((r) => get(r, '成交量', '量', 'volume', 'Volume'));
  const name = String(meta.name ?? meta.stock_name ?? '');
  const code = String(meta.code ?? meta.stock_code ?? '');

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#ffffff',
      borderColor: '#e5e7eb',
      textStyle: { color: '#374151' },
    },
    title: {
      text: name ? `${name} (${code})` : code,
      left: 'center',
      textStyle: { fontSize: 14, color: '#374151' },
    },
    grid: [
      { left: '10%', right: '8%', top: '15%', height: '50%' },
      { left: '10%', right: '8%', top: '72%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { color: '#9ca3af' } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, axisLabel: { color: '#9ca3af' }, splitLine: { lineStyle: { color: '#f3f4f6' } } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { color: '#9ca3af' }, splitLine: { lineStyle: { color: '#f3f4f6' } } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
    ],
    series: [
      {
        type: 'candlestick',
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: RED,
          color0: GREEN,
          borderColor: RED,
          borderColor0: GREEN,
        },
      },
      {
        type: 'bar',
        data: volumes,
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: {
          color: (params: { dataIndex: number }) => {
            const idx = params.dataIndex;
            return (ohlc[idx]?.[1] ?? 0) >= (ohlc[idx]?.[0] ?? 0) ? RED : GREEN;
          },
        },
      },
    ],
  };
}

/** 行情柱状图: 涨跌幅 */
export function buildQuoteOption(
  records: Record<string, unknown>[],
  _meta: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!records.length) return null;

  const names: string[] = [];
  const changes: number[] = [];

  for (const r of records) {
    const code = String(r['代码'] ?? r['code'] ?? '');
    const name = String(r['名称'] ?? r['name'] ?? '');
    const pct = Number(r['涨跌幅'] ?? r['change_percent'] ?? 0);
    names.push(`${code} ${name}`);
    changes.push(pct);
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#ffffff',
      borderColor: '#e5e7eb',
      textStyle: { color: '#374151' },
    },
    grid: { left: '12%', right: '8%', top: '10%', bottom: '15%' },
    xAxis: {
      type: 'category',
      data: names,
      axisLabel: { color: '#9ca3af', rotate: 30, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#9ca3af', formatter: '{value}%' },
      splitLine: { lineStyle: { color: '#f3f4f6' } },
    },
    series: [{
      type: 'bar',
      data: changes,
      itemStyle: {
        color: (params: { dataIndex: number }) => changes[params.dataIndex] >= 0 ? RED : GREEN,
      },
      label: {
        show: true,
        position: 'top',
        formatter: (params: { value: number }) => `${params.value >= 0 ? '+' : ''}${params.value.toFixed(2)}%`,
        color: '#6b7280',
        fontSize: 10,
      },
    }],
  };
}

/** 行情表格 */
export function buildQuoteTableData(
  records: Record<string, unknown>[],
): { headers: string[]; rows: string[][] } | null {
  if (!records.length) return null;

  const headers = ['代码', '名称', '最新价', '涨跌幅', '成交量'];
  const rows: string[][] = [];

  for (const r of records) {
    const code = String(r['代码'] ?? r['code'] ?? '');
    const name = String(r['名称'] ?? r['name'] ?? '');
    const price = Number(r['最新价'] ?? r['price'] ?? 0).toFixed(2);
    const pct = Number(r['涨跌幅'] ?? r['change_percent'] ?? 0);
    const vol = Number(r['成交量'] ?? r['量'] ?? r['volume'] ?? 0);
    rows.push([
      code,
      name,
      price,
      `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`,
      `${(vol / 10000).toFixed(0)}万`,
    ]);
  }

  return { headers, rows };
}

/** 趋势折线图 */
export function buildTrendOption(
  records: Record<string, unknown>[],
  meta: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!records.length) return null;

  const cols = colNames(records);
  const dateCol = cols.find((c) => /日期|date/i.test(c)) ?? cols[0];
  const valueCols = cols.filter((c) => c !== dateCol && !isNaN(Number(records[0][c])));
  if (!valueCols.length) return null;

  const dates = records.map((r) => String(r[dateCol] ?? ''));
  const title = String(meta.title ?? meta.stock_name ?? '趋势图');

  const series = valueCols.map((col, i) => ({
    name: col,
    type: 'line',
    smooth: true,
    data: records.map((r) => Number(r[col] ?? 0)),
    lineStyle: { color: LINE_COLORS[i % LINE_COLORS.length], width: 2 },
    itemStyle: { color: LINE_COLORS[i % LINE_COLORS.length] },
    symbol: 'circle',
    symbolSize: 4,
  }));

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#ffffff',
      borderColor: '#e5e7eb',
      textStyle: { color: '#374151' },
    },
    title: {
      text: title,
      left: 'center',
      textStyle: { fontSize: 14, color: '#374151' },
    },
    legend: valueCols.length > 1 ? {
      bottom: 0,
      textStyle: { color: '#6b7280', fontSize: 10 },
    } : undefined,
    grid: { left: '10%', right: '8%', top: '15%', bottom: '15%' },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#9ca3af' },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#9ca3af' },
      splitLine: { lineStyle: { color: '#f3f4f6' } },
    },
    series,
  };
}

/** 通用表格: 任意 DataFrame → TableData */
function buildGenericTable(records: Record<string, unknown>[]): { headers: string[]; rows: string[][] } | null {
  if (!records.length) return null;

  const headers = colNames(records);
  const rows = records.map((r) =>
    headers.map((h) => {
      const v = r[h];
      if (typeof v === 'number') return v.toLocaleString();
      return String(v ?? '');
    }),
  );

  return { headers, rows };
}
