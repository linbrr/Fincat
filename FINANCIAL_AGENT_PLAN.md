# 私有化个人金融助手 (Financial Agent) 修改框架

## Context

用户要将 Nanobot 核心引擎改造为 **私有化个人金融助手**，专注于股票交易场景。核心需求：

- **Policy Engine**：硬规则防火墙（风控、权限、HITL 人机协同）✅ 已完成
- **Memory System**：基于 SQLite 的分层记忆（持仓、交易、反思、知识库）✅ 已完成
- **Context Builder**：动态上下文（用户画像、持仓、RAG、实时行情）← 下一步
- **Orchestration**：Master-Slave 多 Agent 协作框架
- **Evolution Engine**：自进化引擎（代码生成、沙箱、回测、Skill 注册）

---

## 设计决策：用户画像存储位置

**USER.md（文件方式）**：
- 用户基本信息（姓名、语言、时区）
- 金融用户画像（风险偏好、交易习惯、关注板块）
- 人工可编辑

**SQLite（程序读写）**：
- assets（客观数据，自动更新）
- transactions（客观数据，自动记录）
- reflection_vault（反思片段，自动生成）
- knowledge_base（知识库）

---

## 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Master Orchestrator                               │
│                         (意图识别 / 任务分发 / 用户沟通)                      │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────────┐
        │                       │                           │
        ▼                       ▼                           ▼
┌───────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Data-Agent   │     │  Market-Agent   │     │  Quant-Agent    │
│  (SQL 账目)   │     │  (行情/新闻)    │     │  (Python 量化)  │
└───────────────┘     └─────────────────┘     └─────────────────┘
                                │
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Policy Engine (防火墙)                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ 风控规则     │  │ 权限规则     │  │ HITL 挂起   │  │ Reflection Bus │ │
│  │ 单笔限额     │  │ 禁止根目录写 │  │ execute_trade│  │ Observation触发│ │
│  │ 止损线       │  │ 禁止修改env  │  │ transfer     │  │ 反思片段存储   │ │
│  │ 日内频率     │  │              │  │              │  │                │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Context Builder (扩展现有类)                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ System Prompt│  │ User Profile  │  │ Short-term   │  │ RAG Context    │ │
│  │ 金融专家人设  │  │ USER.md 提取 │  │ History 摘要 │  │ SQLite 检索   │ │
│  │ +交易守则    │  │ 风险偏好     │  │ 最近N轮      │  │ 历史交易经验  │ │
│  │              │  │ 持仓资产     │  │              │  │                │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘ │
│                                              │                              │
│                                    ┌──────────────┐  ┌────────────────┐     │
│                                    │ Market State │  │ 工具定义       │     │
│                                    │ 实时行情快照 │  │ (ToolRegistry) │     │
│                                    └──────────────┘  └────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Tiered Memory System (混合存储)                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────┐│
│  │ Hot (In-memory) │  │ Warm (SQLite)   │  │ Cold (SQLite + sqlite-vec) ││
│  │                  │  │                  │  │                              ││
│  │ Session cache    │  │ assets 表        │  │ reflection_vault 表 (向量)   ││
│  │ 当前会话         │  │ transactions 表  │  │ knowledge_base 表 (向量)     ││
│  │                  │  │ active_tasks 表 │  │                              ││
│  └──────────────────┘  └──────────────────┘  └──────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ USER.md (文件存储，人工可编辑)                                            ││
│  │ - 用户基本信息、时区、语言                                                ││
│  │ - 金融用户画像：风险偏好、交易习惯、关注板块                               ││
│  └─────────────────────────────────────────────────────────────────────────┘│
                                │
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Evolution Engine (自进化)                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐   │
│  │ Discovery    │  │ Sandbox Exec │  │ Backtest    │  │ Skill Registry│   │
│  │ 工具不足检测 │  │ bwrap 沙箱  │  │ 历史K线验证 │  │ 验证通过写入  │   │
│  │ 代码生成任务 │  │ 执行Python   │  │              │  │ skills/ 注册  │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AgentLoop + AgentRunner (现有循环)                         │
│                     Thought → Action → Observation                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 组件清单与实现顺序

### Phase 1: 核心基础设施

#### 1.1 新建 `fincat/agent/policy.py` — Policy Engine

**职责**：硬规则防火墙，所有交易相关操作必须先过此关

```python
class PolicyResult(Enum):
    ALLOW = "allow"
    DENY = "deny"
    SUSPEND = "suspend"  # HITL 需用户确认

@dataclass
class PolicyViolation:
    rule: str
    reason: str
    details: dict

class PolicyEngine:
    def __init__(self, db: SQLiteConnection, config: PolicyConfig):
        self.db = db
        self.config = config

    async def check_trade(self, trade: TradeIntent) -> tuple[PolicyResult, PolicyViolation | None]:
        """检查单笔交易"""
        # 1. 单笔限额检查
        # 2. 止损线检查
        # 3. 日内频率检查
        # 4. 持仓集中度检查
        pass

    async def check_permissions(self, action: ActionIntent) -> tuple[PolicyResult, PolicyViolation | None]:
        """检查操作权限"""
        # 禁止根目录写
        # 禁止修改环境变量
        pass

    def should_suspend(self, tool_name: str) -> bool:
        """HITL: 敏感操作挂起"""
        return tool_name in ("execute_trade", "transfer_funds")

    async def suspend_and_wait(self, suspended_op: SuspendedOperation) -> bool:
        """挂起操作等待用户确认 (返回 True=确认, False=拒绝)"""
        # 发布 SuspendedEvent 到 UI
        # 等待外部确认回调
        pass
```

**钩子集成点**：`AgentRunner._execute_tools()` 之前调用 `PolicyEngine.check_trade()`

---

#### 1.2 新建 `fincat/agent/memory/sqlite_store.py` — SQLite 记忆存储

**职责**：替代现有 MemoryStore 的文件 I/O，实现分层记忆

```python
# 数据库 schema
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    cost_basis REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,  -- BUY/SELL
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    decision_reason TEXT,  -- Agent 当时的决策理由
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS reflection_vault USING vec0(
    embedding REAL[768],
    content TEXT,
    timestamp TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_base USING vec0(
    embedding REAL[768],
    content TEXT,
    source TEXT,
    timestamp TEXT
);
"""

class SQLiteMemoryStore:
    """分层记忆存储"""

    def get_assets(self) -> list[Asset]: ...
    def get_transactions(self, days: int = 30) -> list[Transaction]: ...
    def get_user_profile(self, key: str) -> str | None: ...
    def search_reflections(self, query_embedding: list[float], top_k: int = 5) -> list[str]: ...
    def add_reflection(self, content: str, embedding: list[float]) -> None: ...
    def add_transaction(self, tx: Transaction) -> None: ...
```

**迁移策略**：保留现有 MemoryStore 接口，新增 `SQLiteMemoryStore` 并行运行，逐步迁移

---

### Phase 2: 核心组件扩展

#### 2.1 扩展 `fincat/agent/context.py` — 金融上下文（最小改动方案）

**改动说明**：不新建类，在现有 ContextBuilder 上扩展方法

```python
def build_financial_context(
    self,
    memory_store: SQLiteMemoryStore | None = None,
    symbols: list[str] | None = None,
) -> str:
    """构建金融上下文（追加到 system prompt 末尾）"""
    parts = []

    # 1. 从 USER.md 读取金融用户画像
    user_content = self.memory.read_user()

    # 2. 从 SQLite 读取客观数据
    if memory_store:
        # Current Positions
        assets = memory_store.get_all_assets()
        if assets:
            parts.append("## Current Positions\n" + "\n".join(
                f"- {a.symbol}: {a.quantity} shares @ ${a.avg_price:.2f}"
                for a in assets
            ))

        # Recent Trades (last 7 days)
        txs = memory_store.get_transactions(days=7)
        if txs:
            parts.append("## Recent Trades\n" + "\n".join(
                f"- {tx.timestamp[:10]} {tx.action} {tx.quantity} {tx.symbol} @ ${tx.price}"
                for tx in txs[:10]
            ))

        # Active Tasks
        tasks = memory_store.get_active_tasks()
        if tasks:
            parts.append("## Active Tasks\n" + "\n".join(
                f"- {t.task_id}: {t.status} ({t.progress*100:.0f}%)"
                for t in tasks
            ))

    return "\n\n".join(parts) if parts else ""
```

**修改点**：
- `build_system_prompt()` 新增可选参数 `include_financial: bool = False`
- 末尾追加 `build_financial_context()` 的结果

---

#### 2.2 修改 `fincat/agent/loop.py` — 集成 Policy Engine ✅ 已完成

**修改点**：

1. `AgentLoop.__init__()` 添加 `PolicyEngine` 实例
2. `_run_agent_loop()` 或 `AgentRunner._execute_tools()` 前添加 Policy 检查
3. 对于 `SUSPEND` 状态，返回特殊响应等待 UI 确认

```python
# loop.py 修改
class AgentLoop:
    def __init__(self, ..., policy_engine: PolicyEngine | None = None):
        self.policy_engine = policy_engine or PolicyEngine()

    async def _run_agent_loop(self, ...):
        # ... 现有逻辑 ...
        # 在 tool execution 前
        hook_ctx = AgentHookContext(...)
        await hook.before_iteration(hook_ctx)

        # Policy check before tools
        for tc in response.tool_calls:
            result, violation = await self.policy_engine.check_action(tc.name, tc.arguments)
            if result == PolicyResult.DENY:
                # 替换 tool_call 为 error message
                ...
            elif result == PolicyResult.SUSPEND:
                # 挂起，等待 UI 确认
                confirmed = await self.policy_engine.suspend_and_wait(...)
                if not confirmed:
                    # 用户拒绝，替换为拒绝消息
                    ...
```

---

#### 2.3 新建 `fincat/agent/orchestrator.py` — Master-Slave 编排

**职责**：多 Agent 协作，任务分发

```python
class Orchestrator:
    """Master Agent 编排器"""

    def __init__(self, agents: dict[str, AgentLoop]):
        self.agents = agents
        self.master = agents["master"]

    async def route_task(self, intent: UserIntent) -> str:
        """识别意图，返回对应 Agent ID"""
        # Data-Agent: SQL 查询、账目计算
        # Market-Agent: 行情、新闻
        # Quant-Agent: 量化计算、策略回测
        # Master: 其他通用对话
        pass

    async def execute_subagent(
        self,
        agent_id: str,
        task: str,
    ) -> str:
        """执行子 Agent 任务"""
        agent = self.agents[agent_id]
        result = await agent.process_direct(task, session_key=f"{agent_id}:task")
        return result.content
```

---

### Phase 3: Evolution Engine

#### 3.1 新建 `fincat/agent/evolution.py` — 自进化引擎

```python
class EvolutionEngine:
    """Discovery → Sandbox → Backtest → Skill Registry"""

    def __init__(
        self,
        workspace: Path,
        backtest_engine: BacktestEngine,
        skill_registry: SkillRegistry,
    ):
        self.workspace = workspace
        self.backtest = backtest_engine
        self.registry = skill_registry

    async def maybe_evolve(self, failed_tool_call: FailedToolCall) -> bool:
        """检测是否需要生成新工具"""
        if not self._should_evolve(failed_tool_call):
            return False

        # 1. Discovery: 生成代码
        code = await self._generate_code(failed_tool_call)

        # 2. Sandbox: 安全执行
        result = await self._sandbox_execute(code)

        # 3. Backtest: 历史验证
        if result.success:
            backtest_result = await self.backtest.run(code, symbols=self._relevant_symbols())
            if backtest_result.sharpe_ratio > 1.0:
                # 4. 注册为 Skill
                await self.registry.register(code)
                return True
        return False
```

---

### Phase 4: 工具层

#### 4.1 新建 `fincat/agent/tools/trading.py` — 交易工具

```python
class ExecuteTradeTool(Tool):
    name = "execute_trade"
    description = "执行股票交易"
    concurrency_safe = False  # 不允许并发

    async def execute(self, symbol: str, action: str, quantity: int, price: float | None = None) -> str:
        """实际执行交易（需对接券商 API）"""
        # 实际对接：老虎证券 / 富途 / Alpaca / IBKR
        pass

class TransferFundsTool(Tool):
    name = "transfer_funds"
    description = "资金转账"

class GetPositionTool(Tool):
    name = "get_position"
    description = "查询持仓"

class GetAccountTool(Tool):
    name = "get_account"
    description = "查询账户信息"
```

#### 4.2 新建 `fincat/agent/tools/market.py` — 行情工具

```python
class MarketDataClient:
    """行情数据客户端（聚合多个数据源）"""

    async def get_snapshot(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        """获取实时行情"""
        # 数据源：Alpha Vantage / Yahoo Finance / Tushare / 聚合
        pass

    async def get_historical(
        self,
        symbol: str,
        start: date,
        end: date,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """获取历史 K 线"""
        pass

class WebSearchFinancialTool(Tool):
    """金融新闻/公告搜索"""
    pass
```

---

## 关键文件修改清单

| 文件 | 操作 | 状态 |
|------|------|------|
| `fincat/agent/loop.py` | 修改 | ✅ 已完成 |
| `fincat/agent/runner.py` | 修改 | ✅ 已完成 |
| `fincat/agent/context.py` | 修改 | ✅ 已完成 |
| `fincat/agent/policy.py` | **新建** | ✅ 已完成 |
| `fincat/agent/memory_sqlite.py` | **新建** | ✅ 已完成 |
| `fincat/agent/orchestrator.py` | **新建** | ✅ 已完成 |
| `fincat/agent/evolution.py` | **新建** | ✅ 已完成 |
| `fincat/agent/tools/trading.py` | **新建** | ✅ 已完成 |
| `fincat/agent/tools/market.py` | 整合 FinanceMCP | ✅ 外部服务 |
| `fincat/config/schema.py` | 修改 | ✅ 已完成 |
| `fincat/templates/financial/system_prompt.md` | 整合到 ContextBuilder | ✅ 已完成 |

---

## 当前进度

```
✅ PolicyEngine (policy.py) — 完成
✅ SQLiteMemoryStore (memory_sqlite.py) — 完成
✅ ContextBuilder 扩展 — 完成
   ├── build_financial_context() 方法
   └── build_system_prompt() 扩展
✅ Orchestrator (orchestrator.py) — 完成
   ├── TaskType 分类
   ├── route_task() 主从分发
   └── SubAgentPool 并行执行
✅ Evolution Engine (evolution.py) — 完成
   ├── _should_evolve() 发现
   ├── _generate_code() 生成
   ├── _sandbox_execute() 沙箱
   ├── _run_backtest() 回测
   └── _register_skill() 注册
✅ Trading Tools (tools/trading.py) — 完成
   ├── ExecuteTradeTool
   ├── CancelOrderTool
   ├── GetPositionTool
   ├── GetAccountTool
   ├── GetQuoteTool
   └── AlpacaAdapter 券商适配器
🔜 FinanceMCP 整合 — 只需配置
   └── 公共云服务 / 本地部署
```

---

## 依赖关系图

```
Phase 1 (基础设施) ✅ 完成
├── policy.py (PolicyEngine)
├── memory_sqlite.py (SQLiteMemoryStore)
└── config/schema.py (PolicyConfig)

Phase 2 (核心组件) ✅ 完成
├── context.py 扩展 (最小改动方案)
│   └── 依赖: memory_sqlite.py
├── orchestrator.py (Master-Slave) ✅
└── evolution.py (自进化引擎) ✅

Phase 3 (工具层) ✅ 完成
├── tools/trading.py (交易工具) ✅
└── FinanceMCP (行情工具) — 外部 MCP 服务 ✅

Phase 4 (Evolution) ✅ 完成
└── evolution.py (自进化引擎)
```

---

## 验证方案

### 单元测试
```bash
# Policy Engine 测试
pytest tests/agent/test_policy.py -v

# SQLite Memory Store 测试
pytest tests/agent/test_sqlite_store.py -v

# Context Builder 测试
pytest tests/agent/test_context_financial.py -v
```

### 集成测试
```bash
# 1. 启动带 PolicyEngine 的 AgentLoop
fincat agent --enable-policy

# 2. 模拟交易请求
# - 限额检查: 大额单笔应被拒绝
# - HITL: execute_trade 应返回 SUSPEND 状态

# 3. 验证记忆层
# - 交易后检查 assets 表
# - 验证 reflection_vault 写入
```

### 端到端测试场景
1. **正常交易流程**: 查询持仓 → 分析 → 下单 → 验证 assets 表更新
2. **风控拦截**: 单笔超过限额 → Policy DENY
3. **HITL 流程**: execute_trade → SUSPEND → 用户确认 → 执行
4. **多 Agent 协作**: Quant-Agent 计算 → Data-Agent 验证账目 → Master 汇总

---

## 实施建议

1. ✅ **PolicyEngine 已完成** — 安全相关的基础防火墙
2. ✅ **SQLiteMemoryStore 已完成** — 混合存储方案（USER.md + SQLite）
3. ✅ **Context Builder 扩展** — 最小改动方案，在现有类上扩展方法
4. ✅ **Orchestrator/Evolution/Trading Tools 已完成**
5. 🔜 **FinanceMCP 整合** — 只需配置即可使用完整行情数据

## FinanceMCP 配置示例

```json
// fincat 配置文件中添加 MCP 服务器
{
  "tools": {
    "mcp_servers": {
      "finance-mcp": {
        "type": "streamableHttp",
        "url": "https://finvestai.top/mcp",
        "headers": {
          "X-Tushare-Token": "你的tushare令牌"
        },
        "enabled_tools": ["*"]
      }
    }
  }
}
```

可用的 FinanceMCP 工具：
- `mcp_finance-mcp_stock_data` — 股票/加密 + MACD/RSI/KDJ/BOLL/MA
- `mcp_finance-mcp_finance_news` — 财经新闻搜索
- `mcp_finance-mcp_macro_econ` — 宏观经济数据
- `mcp_finance-mcp_company_performance` — A股财务分析
- `mcp_finance-mcp_money_flow` — 资金流向
- `mcp_finance-mcp_index_data` — 指数数据
- `mcp_finance-mcp_hot_news_7x24` — 7×24 热点新闻
