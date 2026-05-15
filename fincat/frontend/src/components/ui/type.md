// AIMessageBlock.tsx
// OpenClaw / Claude / Cursor 风格
// shadcn + Tailwind + ECharts
// Vite + React 18 + TS

import { useState } from "react"
import ReactECharts from "echarts-for-react"
import {
  ChevronDown,
  ChevronRight,
  Wrench,
  Brain,
  Table2,
  ChartLine,
} from "lucide-react"

const option = {
  backgroundColor: "transparent",

  tooltip: {
    trigger: "axis",
    backgroundColor: "#111827",
    borderColor: "#1e293b",
    textStyle: {
      color: "#e2e8f0",
    },
  },

  grid: {
    left: 20,
    right: 20,
    top: 20,
    bottom: 20,
  },

  xAxis: {
    type: "category",
    boundaryGap: false,
    data: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],

    axisLine: {
      lineStyle: {
        color: "#334155",
      },
    },

    axisLabel: {
      color: "#64748b",
    },
  },

  yAxis: {
    type: "value",

    splitLine: {
      lineStyle: {
        color: "#1e293b",
      },
    },

    axisLabel: {
      color: "#64748b",
    },
  },

  series: [
    {
      data: [820, 932, 901, 934, 1290, 1330, 1520],
      type: "line",
      smooth: true,

      lineStyle: {
        color: "#10b981",
        width: 3,
      },

      areaStyle: {
        color: "rgba(16,185,129,0.12)",
      },

      symbol: "none",
    },
  ],
}

function ExpandableBlock({
  title,
  icon,
  children,
}: {
  title: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/40">
      <button
        onClick={() => setOpen(!open)}
        className="
        flex
        w-full
        items-center
        justify-between
        px-4
        py-3
        text-left
        transition-all
        hover:bg-slate-800/40
        "
      >
        <div className="flex items-center gap-3">
          <div className="text-slate-400">
            {icon}
          </div>

          <span className="text-sm font-medium text-slate-200">
            {title}
          </span>
        </div>

        {open ? (
          <ChevronDown className="h-4 w-4 text-slate-500" />
        ) : (
          <ChevronRight className="h-4 w-4 text-slate-500" />
        )}
      </button>

      {open && (
        <div
          className="
          border-t
          border-slate-800
          p-4
          animate-in
          fade-in
          slide-in-from-top-2
          duration-200
          "
        >
          {children}
        </div>
      )}
    </div>
  )
}

export default function AIMessageBlock() {
  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6">
      {/* User */}
      <div className="flex justify-end">
        <div
          className="
          max-w-[75%]
          rounded-2xl
          bg-emerald-500
          px-5
          py-4
          text-black
          shadow-lg
          "
        >
          Analyze NVIDIA's short-term trend and risk.
        </div>
      </div>

      {/* Assistant */}
      <div className="flex gap-4">
        {/* Avatar */}
        <div
          className="
          flex
          h-10
          w-10
          shrink-0
          items-center
          justify-center
          rounded-2xl
          bg-emerald-500/15
          font-semibold
          text-emerald-400
          "
        >
          AI
        </div>

        {/* Content */}
        <div className="flex-1 space-y-4">
          {/* Main Response */}
          <div
            className="
            rounded-2xl
            border
            border-slate-800
            bg-slate-900/40
            px-5
            py-4
            backdrop-blur
            "
          >
            <div className="space-y-4">
              <p className="leading-7 text-slate-200">
                NVIDIA maintains strong institutional momentum
                driven by continued AI infrastructure demand.
                Earnings guidance exceeded expectations and
                semiconductor sector rotation remains positive.
              </p>

              <p className="leading-7 text-slate-300">
                Short-term volatility may increase ahead of
                upcoming macroeconomic events, but overall
                trend structure remains bullish.
              </p>
            </div>
          </div>

          {/* Reasoning */}
          <ExpandableBlock
            title="Reasoning Process"
            icon={<Brain className="h-4 w-4" />}
          >
            <div className="space-y-3 text-sm leading-7 text-slate-300">
              <p>
                • Evaluated institutional buying pressure
              </p>

              <p>
                • Compared sector momentum across AI chipmakers
              </p>

              <p>
                • Reviewed earnings growth expectations
              </p>

              <p>
                • Analyzed implied volatility conditions
              </p>
            </div>
          </ExpandableBlock>

          {/* Tool Calls */}
          <ExpandableBlock
            title="Tool Calls"
            icon={<Wrench className="h-4 w-4" />}
          >
            <div className="space-y-3">
              {[
                "Market Data API",
                "Financial News Search",
                "Volatility Analyzer",
                "Earnings Analysis Engine",
              ].map((tool) => (
                <div
                  key={tool}
                  className="
                  flex
                  items-center
                  justify-between
                  rounded-xl
                  border
                  border-slate-800
                  bg-slate-950/40
                  px-4
                  py-3
                  "
                >
                  <span className="text-sm text-slate-300">
                    {tool}
                  </span>

                  <div
                    className="
                    rounded-full
                    bg-emerald-500/15
                    px-2
                    py-1
                    text-xs
                    text-emerald-400
                    "
                  >
                    Success
                  </div>
                </div>
              ))}
            </div>
          </ExpandableBlock>

          {/* Chart */}
          <ExpandableBlock
            title="Price Trend Analysis"
            icon={<ChartLine className="h-4 w-4" />}
          >
            <div
              className="
              rounded-2xl
              border
              border-slate-800
              bg-[#0b1120]
              p-3
              "
            >
              <ReactECharts
                option={option}
                style={{ height: 320 }}
              />
            </div>
          </ExpandableBlock>

          {/* Table */}
          <ExpandableBlock
            title="Institutional Positioning"
            icon={<Table2 className="h-4 w-4" />}
          >
            <div
              className="
              overflow-hidden
              rounded-2xl
              border
              border-slate-800
              "
            >
              <table className="w-full">
                <thead className="bg-slate-900/80">
                  <tr className="border-b border-slate-800">
                    <th className="px-4 py-3 text-left text-sm font-medium text-slate-400">
                      Institution
                    </th>

                    <th className="px-4 py-3 text-left text-sm font-medium text-slate-400">
                      Position
                    </th>

                    <th className="px-4 py-3 text-left text-sm font-medium text-slate-400">
                      Change
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {[
                    {
                      name: "BlackRock",
                      position: "$2.4B",
                      change: "+4.2%",
                    },
                    {
                      name: "Vanguard",
                      position: "$1.9B",
                      change: "+2.8%",
                    },
                    {
                      name: "Bridgewater",
                      position: "$740M",
                      change: "-1.1%",
                    },
                  ].map((row) => (
                    <tr
                      key={row.name}
                      className="
                      border-b
                      border-slate-800
                      bg-slate-950/20
                      transition-all
                      hover:bg-slate-900/40
                      "
                    >
                      <td className="px-4 py-4 text-sm text-slate-200">
                        {row.name}
                      </td>

                      <td className="px-4 py-4 font-mono tabular-nums text-sm text-slate-300">
                        {row.position}
                      </td>

                      <td
                        className={`
                        px-4
                        py-4
                        text-sm
                        font-medium
                        ${
                          row.change.startsWith("+")
                            ? "text-emerald-400"
                            : "text-red-400"
                        }
                        `}
                      >
                        {row.change}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </ExpandableBlock>
        </div>
      </div>
    </div>
  )
}