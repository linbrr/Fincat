# fincat 自定义前端方案

## 为什么需要自定义前端

1. **图表不显示**：Chainlit 内置 UI 的 Plotly 渲染不可靠
2. **固定预测区域**：Agent 主动预测用户需求时，需要一个不随聊天滚动的固定区域显示
3. **侧边栏限制**：Chainlit 侧边栏关闭后无法重新打开，无法添加自定义组件
4. **金融专用组件**：K 线图、行情表格等需要专业图表库（ECharts）

## 架构

```
┌─────────────────────────────────────────────────────┐
│  自定义 React 前端                                    │
│  ┌──────────────┐  ┌──────────────────────────────┐ │
│  │  聊天面板     │  │  固定看板（不随聊天滚动）      │ │
│  │  - 消息列表   │  │  - 主动预测/推荐             │ │
│  │  - 流式输出   │  │  - K线图 (ECharts)           │ │
│  │  - 工具步骤   │  │  - 实时行情表格              │ │
│  │  - 输入框     │  │  - 财务指标                  │ │
│  └──────────────┘  └──────────────────────────────┘ │
│         ↕ Socket.IO (@chainlit/react-client)         │
├─────────────────────────────────────────────────────┤
│  Chainlit 后端 (app.py → AgentLoop)                  │
│  Agent 逻辑完全不动，仅新增事件推送                      │
└─────────────────────────────────────────────────────┘
```

## 关于 Node.js 依赖

**开发时**：需要 Node.js（构建前端）
**普通用户**：不需要！构建后的静态文件由 Python 直接 serve，用户只需 `uv run fincat chat`

```
开发流程:  npm run dev (热更新)  ──→  开发者
发布流程:  npm run build         ──→  dist/ (纯静态文件)
用户使用:  uv run fincat chat    ──→  Python serve dist/ 文件
```

## 实施步骤

### 第一步：基础聊天 + 固定看板

**目标**：跑通通信，左聊天右看板布局。

**技术栈**：Vite + React 18 + TypeScript + Tailwind CSS + ECharts

**前端结构**：
```
fincat/frontend/
├── package.json
├── vite.config.ts
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx               # 左右分栏布局
    ├── api/chainlit.ts       # @chainlit/react-client 封装
    ├── hooks/
    │   ├── useChat.ts        # 消息收发
    │   └── usePrediction.ts  # 接收主动预测数据
    ├── components/
    │   ├── ChatPanel/        # 聊天面板
    │   └── DataPanel/        # 固定看板（预测 + 图表）
    └── types/
```

**后端改动**（`app.py`）：
- 新增 `_push_data()` 函数，通过 Chainlit 事件推送结构化数据
- 工具执行后推送图表数据
- Dream/预测引擎触发时推送预测内容

```python
async def _push_data(event_type: str, payload: dict):
    """推送结构化数据到自定义前端"""
    await cl.context.emitter.emit("fincat_data", {
        "type": event_type,
        "payload": payload,
    })
```

### 第二步：金融组件 + 预测联动

**目标**：图表正常显示，预测区域自动更新。

**金融组件**（ECharts）：
| 组件 | 数据源 | 说明 |
|------|--------|------|
| KlineChart | `stock_kline` | K 线 + 成交量 |
| QuoteTable | `stock_quote` | 涨跌色行情表 |
| FinancialChart | `stock_financial` | 财务指标柱状图 |
| PredictionCard | Dream/预测引擎 | 主动推荐卡片 |

**预测推送逻辑**：
```python
# agent/loop.py 或 dream 任务中
async def _on_prediction(prediction: dict):
    """当 Agent 预测到用户需求时，推送到前端固定区域"""
    await _push_data("prediction", {
        "title": prediction["title"],
        "content": prediction["content"],
        "actions": prediction.get("actions", []),  # 可点击的操作
    })
```

## 后端改动清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `app.py` | 新增 `_push_data()` | 事件推送函数 |
| `app.py` | 保留现有钩子 | 后端逻辑不动 |
| `agent/tools/akshare.py` | 补全 `store_chart_data()` | 金融工具存结构化数据 |
| `chart_builder.py` | 不变 | — |

## 验证方式

1. `cd fincat/frontend && npm install && npm run dev` 启动前端
2. `uv run fincat chat` 启动后端
3. 浏览器打开 `http://localhost:5173`
4. 问「茅台最近走势」→ 右边显示 K 线图
5. 等待预测推送 → 右边固定区域显示推荐内容
