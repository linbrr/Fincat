import type { QuoteRow } from '../../types';

interface Props {
  data: QuoteRow[];
}

export function QuoteTable({ data }: Props) {
  if (!data.length) return null;

  return (
    <div className="h-full overflow-auto p-3">
      <div className="rounded-xl border border-gray-100 bg-white overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50/80 text-left">
              <th className="px-3 py-2 font-medium text-gray-500">代码</th>
              <th className="px-3 py-2 font-medium text-gray-500">名称</th>
              <th className="px-3 py-2 text-right font-medium text-gray-500">价格</th>
              <th className="px-3 py-2 text-right font-medium text-gray-500">涨跌%</th>
              <th className="px-3 py-2 text-right font-medium text-gray-500">成交量</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => {
              const isUp = row.changePercent > 0;
              const isDown = row.changePercent < 0;
              const color = isUp ? 'text-red-600' : isDown ? 'text-green-600' : 'text-gray-600';
              return (
                <tr key={row.code} className="border-b border-gray-50 hover:bg-gray-50/50 transition-colors">
                  <td className="px-3 py-2 font-mono text-gray-400 text-[11px]">{row.code}</td>
                  <td className="px-3 py-2 font-medium text-gray-700">{row.name}</td>
                  <td className={`px-3 py-2 text-right font-mono font-medium ${color}`}>
                    {row.price.toFixed(2)}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${color}`}>
                    <span className={`inline-block rounded px-1 py-0.5 text-[11px] ${
                      isUp ? 'bg-red-50' : isDown ? 'bg-green-50' : ''
                    }`}>
                      {isUp ? '+' : ''}{row.changePercent.toFixed(2)}%
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-gray-400 font-mono text-[11px]">
                    {(row.volume / 10000).toFixed(0)}万
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
