import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import type { KlineData } from '../../types';

interface Props {
  data: KlineData;
}

export function KlineChart({ data }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current = echarts.init(ref.current);

    const option: echarts.EChartsOption = {
      animation: false,
      title: {
        text: `${data.name} (${data.code})`,
        left: 'center',
        textStyle: { fontSize: 14 },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
      },
      grid: [
        { left: '10%', right: '8%', top: '15%', height: '50%' },
        { left: '10%', right: '8%', top: '72%', height: '18%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: data.dates,
          gridIndex: 0,
          axisLabel: { show: false },
        },
        {
          type: 'category',
          data: data.dates,
          gridIndex: 1,
        },
      ],
      yAxis: [
        { scale: true, gridIndex: 0 },
        { scale: true, gridIndex: 1, splitNumber: 2 },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      ],
      series: [
        {
          type: 'candlestick',
          data: data.ohlc,
          xAxisIndex: 0,
          yAxisIndex: 0,
          itemStyle: {
            color: '#ef4444',       // up = red (Chinese convention)
            color0: '#22c55e',      // down = green
            borderColor: '#ef4444',
            borderColor0: '#22c55e',
          },
        },
        {
          type: 'bar',
          data: data.volumes,
          xAxisIndex: 1,
          yAxisIndex: 1,
          itemStyle: {
            color: (params: any) => {
              const idx = params.dataIndex;
              return data.ohlc[idx]?.[1] >= data.ohlc[idx]?.[0]
                ? '#ef4444'
                : '#22c55e';
            },
          },
        },
      ],
    };

    chartRef.current.setOption(option);

    const ro = new ResizeObserver(() => chartRef.current?.resize());
    ro.observe(ref.current);

    return () => {
      ro.disconnect();
      chartRef.current?.dispose();
    };
  }, [data]);

  return <div ref={ref} className="h-full w-full" />;
}
