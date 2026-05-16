---
name: initiating-coverage
description: "股票深度研究覆盖报告：一次生成完整公司研究+财务模型+估值定价+投资建议，输出 coverage.md 到 workspace 作为长期记忆。当用户说'初次覆盖'、'写研究报告'、'深度覆盖'、'股票研究'、'覆盖这只股票'时触发。"
---

# Initiating Coverage

对一只股票进行完整的专业投行级别的深度研究，输出 `coverage.md` 到 workspace，作为该标的的长期研究记忆。

**与 LangAlpha 的区别**：
- LangAlpha 分 5 个独立 task（研究→模型→估值→图表→组装），fincat 合并为 1 个 unified task
- LangAlpha 输出 DOCX（需 Word），fincat 输出 MD（workspace 原生格式，ContextBuilder 直接加载）
- LangAlpha 用 PTC + MCP 云端执行，fincat 用 akshare 直连 + matplotlib 图表
- 聚焦 A 股/港股上市公司（600000.SH、000000.SZ、0700.HK 等格式）

## 工作流

### Step 0：确认股票标的

询问用户要研究哪只股票，确认：
- 股票代码（如 600519.SH）
- 市场（A 股/港股/美股）
- 研究目的（投资建仓、持仓跟踪、竞争对比等）

### Step 1：公司基本情况（`stock_quote` + `stock_news` + `stock_block`）

用 `stock_quote(symbols=[代码], market="a")` 获取实时行情：
- 最新价、涨跌幅、成交量、成交额、市值、PE、PB

用 `stock_news(symbol=代码, max_results=10)` 获取公司新闻和公告

用 `stock_block(type="industry")` 确认所属行业和板块

**输出：公司基本情况章节**

## 1. 公司概况

**股票代码**：{代码}（{公司名称}）
**交易市场**：A 股（上交所/深交所）
**所属行业**：{行业}
**子行业**：{子行业}

**当前行情**（{日期}）：
| 指标 | 数值 |
|------|------|
| 最新价 | {X}元 |
| 涨跌额 | +{X}% |
| 成交量 | {X}万手 |
| 成交额 | {X}亿元 |
| 总市值 | {X}亿元 |
| PE(TTM) | {X}倍 |
| PB | {X}倍 |

**公司主营业务**：
（根据公司名称+行业常识推断，2-3句话）

**最新新闻**：
- {新闻1}（{日期}）
- {新闻2}（{日期}）

### Step 2：历史 K 线与价格走势（`stock_kline`）

用 `stock_kline(symbol=代码, period="daily", start_date=2年前)` 获取 2 年日线数据

分析关键价格：
- 52 周高低点及时间
- 近期趋势（20日均线方向）
- 成交量变化（放量/缩量）

**输出：价格走势章节**

## 2. 历史价格走势

**52 周表现**：
- 最高价：{X}元（{日期}）
- 最低价：{X}元（{日期}）
- 当前价处于 52 周区间的 {X}% 分位

**均线系统**：
- MA5: {X}元（{'↑' if MA5>MA10 else '↓'}）
- MA20: {X}元
- MA60: {X}元
- 整体趋势：{'上升通道' / '下降通道' / '震荡整理'}

**成交量特征**：
- 近 20 日均成交量：{X}万手
- 最近放量日期：{日期}（较均量 {+/-X}%）

### Step 3：技术指标分析（`stock_indicator`）

用 `stock_indicator(symbol=代码)` 获取完整技术指标分析

**输出：技术分析章节**

## 3. 技术指标分析

**趋势判断**：{MA5>MA10>MA20>MA60 → 多头排列 / 反之为空头排列 / 其他为震荡}

**MACD**：
- DIF: {X}
- DEA: {X}
- MACD柱：{X}（{'红柱（下跌动能）' / '绿柱（上涨动能）'}）
- 信号：{'MACD 金叉（买入信号）' / 'MACD 死叉（卖出信号）' / '零轴附近震荡'}

**RSI（14日）**：{X} — {'超买区间（>70）' / '超卖区间（<30）' / '中性（30-70）'}

**KDJ**：
- K={X}, D={X}, J={X}
- 状态：{'KDJ 超买' / 'KDJ 超卖' / '中性'}

**布林带**：
- 上轨：{X}元
- 中轨：{X}元
- 下轨：{X}元
- 当前价格处于布林带{'上轨附近（强势）' / '下轨附近（弱势）' / '中轨附近'}

**综合技术结论**：
{基于以上指标，给出 1-2 句话的技术面判断}

### Step 4：基本面财务数据（`stock_financial`）

用 `stock_financial(symbol=代码)` 获取近 4 个季度财务数据

**输出：财务分析章节**

## 4. 基本面财务分析

**关键财务指标**（近 4 季度）：

| 指标 | Q1 | Q2 | Q3 | Q4 | 趋势 |
|------|----|----|----|----|------|
| 营收（亿元） | X | X | X | X | {'↑' / '↓' / '→'} |
| 净利润（亿元） | X | X | X | X | {'↑' / '↓' / '→'} |
| 毛利率 | X% | X% | X% | X% | {'↑' / '↓' / '→'} |
| 净利率 | X% | X% | X% | X% | {'↑' / '↓' / '→'} |
| ROE（年化） | X% | X% | X% | X% | {'↑' / '↓' / '→'} |
| EPS（元） | X | X | X | X | {'↑' / '↓' / '→'} |

**盈利能力分析**：
（根据上表，给出 2-3 句话的盈利能力判断）

**成长性分析**：
- 营收同比增速：{X}%
- 净利润同比增速：{X}%
- 判断：{'高增长' / '稳定增长' / '增速放缓' / '负增长'}

**财务健康度**：
（根据毛利率+净利率+ROE，给出综合评价）

### Step 5：板块与资金流向（`stock_block` + `stock_hsgt`）

用 `stock_block(type="industry", top_n=10)` 查行业板块涨跌

用 `stock_hsgt(direction="north")` 查北向资金近 10 日流向

**输出：板块与资金章节**

## 5. 板块与资金流向

**行业板块对比**：
| 板块 | 今日涨幅 | 近 20 日涨幅 |
|------|---------|-------------|
| {公司所在行业} | {X}% | {X}% |
| 沪深300 | {X}% | {X}% |
| 相对强弱 | {X}% | {X}% |

**北向资金**（近 10 日）：
- 累计净流入：{+X}亿元 / {-X}亿元
- 趋势：{'持续流入（看好）' / '持续流出（看空）' / '来回波动（中性）'}

**板块资金情绪**：
（综合板块涨跌+北向资金，给出 1-2 句话判断）

### Step 6：估值分析（DCF + Comparable）

**6a. 可比公司法（PE/PB/PS）**

获取同业可比公司的关键估值指标：
- 同行业 5-10 家可比公司列表
- PE 中位数、平均值
- PB 中位数、平均值
- PS 中位数、平均值

使用 `valuation_calc(calc_type="percentile_rank", values=[...], value=X)` 计算当前公司 PE/PB 在行业中的百分位排名。

**6b. DCF 估值（简化版）**

基于财务数据，估算 DCF 内在价值。**必须使用 `valuation_calc` 工具进行计算，不要心算：**

1. 计算历史 CAGR：`valuation_calc(calc_type="cagr", begin_value=历史营收, end_value=最新营收, years=N)`
2. 计算 WACC：
   - 先算 CAPM：`valuation_calc(calc_type="capm", risk_free_rate=0.025, beta=X, market_return=0.10)`
   - 再算 WACC：`valuation_calc(calc_type="wacc", equity=E, debt=D, cost_of_equity=Ke, cost_of_debt=Kd, tax_rate=T)`
3. 预测 FCF 并折现：`valuation_calc(calc_type="dcf", cash_flows=[FCF1,FCF2,...,FCF5], wacc_rate=WACC, terminal_growth_rate=g, net_debt=D, shares_outstanding=N)`
4. 敏感性分析：用不同 WACC 和 g 值多次调用 `dcf`，生成敏感性矩阵

**输出：估值分析章节**

## 6. 估值分析

### 6.1 可比公司估值

**行业可比公司**：

| 公司名 | 代码 | PE(TTM) | PB | PS | 市值（亿） |
|--------|------|---------|----|----|----------|
| {公司A} | XXXXXX | X倍 | X倍 | X倍 | X |
| {公司B} | XXXXXX | X倍 | X倍 | X倍 | X |
| {公司C} | XXXXXX | X倍 | X倍 | X倍 | X |
| ... | | | | | |

**估值分位数**：
- 当前 PE {X}倍，处于行业 {X}% 分位（{'偏低' / '合理' / '偏高'}）
- 当前 PB {X}倍，处于行业 {X}% 分位（{'偏低' / '合理' / '偏高'}）

### 6.2 DCF 内在价值估算

**核心假设**：
- 当前营收：{X}亿元
- 未来 5 年营收复合增速：{X}%
- 永续增长率：{X}%
- 加权平均资本成本（WACC）：{X}%

**现金流折现**：

| 年度 | 营收（亿） | 净利率 | 净利润（亿） | FCF（亿） | 折现因子 | 现值（亿） |
|------|-----------|--------|------------|-----------|---------|-----------|
| Y1 | X | X% | X | X | 0.93 | X |
| Y2 | X | X% | X | X | 0.86 | X |
| Y3 | X | X% | X | X | 0.80 | X |
| Y4 | X | X% | X | X | 0.74 | X |
| Y5 | X | X% | X | X | 0.68 | X |

- 5 年 FCF 现值合计：{X}亿元
- 终值：{X}亿元（折现到现值：{X}亿元）
- **DCF 内在价值**：{X}亿元

### 6.3 估值结论

| 估值方法 | 估值结果 | 对应股价 |
|---------|---------|---------|
| 可比PE法 | {X}亿元 | {X}元 |
| 可比PB法 | {X}亿元 | {X}元 |
| DCF法 | {X}亿元 | {X}元 |
| **综合估值** | **{X}亿元** | **{X}元}** |

**当前股价**：{X}元
**综合估值**：{X}元
**上行空间**：{+/-X}%
**估值评级**：{'低估' / '合理' / '高估'}

### Step 7：综合投资建议

综合以上所有分析，给出最终投资建议：

**输出：投资建议章节**

## 7. 综合投资建议

### 7.1 投资评级

**评级**：{强烈推荐 / 推荐 / 中性 / 回避}
**目标价**：{X}元（基于综合估值）
**当前价**：{X}元
**潜在涨幅**：{+/-X}%
**建议仓位**：{轻仓 / 标准仓 / 重仓 / 满仓}

### 7.2 核心投资逻辑

**看多理由**（3-5 条）：
1. {理由1}
2. {理由2}
3. {理由3}

**主要风险**（3 条）：
1. {风险1}
2. {风险2}
3. {风险3}

### 7.3 关键催化剂

- {催化剂1}（预计时间：{时间}）
- {催化剂2}（预计时间：{时间}）

### 7.4 持仓策略

- **建仓区间**：{X}-{X}元
- **止损位**：{X}元（当前价的 -X%）
- **持有期限**：{短期(3个月内) / 中期(6-12个月) / 长期(1年以上)}

### 7.5 跟踪要点

- 每季度财报后重新评估业绩是否达预期
- 关注行业政策变化（如：白酒-消费税、芯片-出口管制等）
- 关注北向资金持续流向（连续净流出超过 5 日需警惕）

### Step 8：生成图表（如可行）

使用 `ExecTool` 或 Python 代码生成关键图表（如支持）：

1. **K线+MA叠加图**：近 1 年日线 + MA5/MA20/MA60
2. **财务指标趋势图**：营收、净利润、毛利率近 4 季度对比
3. **估值对比图**：当前估值 vs 历史分位 vs 行业均值
4. **北向资金流向图**：近 20 日净流入/流出

**图表存储位置**：`{workspace}/coverage/charts/{股票代码}/`
- `kline_1y.png`
- `financial_trend.png`
- `valuation_comparison.png`
- `hsgt_flow.png`

**输出：图表引用**

## 8. 附录：图表

![K线走势图](charts/{代码}/kline_1y.png)
![财务趋势](charts/{代码}/financial_trend.png)
![估值对比](charts/{代码}/valuation_comparison.png)
![北向资金](charts/{代码}/hsgt_flow.png)

### Step 9：保存与记忆

完成所有章节后：
1. 将完整报告保存为 `{workspace}/coverage/{股票代码}_{日期}.md`
2. 同时更新/创建 `{workspace}/coverage/{股票代码}_latest.md`（最新版本链接）
3. 写入 SQLite `knowledge_base` 表（向量检索用）

**输出：文件路径**

---
研究标的：{股票名称}（{代码}）
研究日期：{日期}
研究分析师：fincat
文件路径：coverage/{股票代码}_{日期}.md
---

## 工具映射参考

| LangAlpha MCP 工具 | fincat akshare 工具 | 说明 |
|------------------|---------------------|------|
| `get_realtime_quote` | `stock_quote` | 实时行情 |
| `get_historical_price` | `stock_kline` | K线历史数据 |
| `get_market_overview` | `stock_block` + `stock_quote` | 板块+大盘行情 |
| `get_north_bound_flow` | `stock_hsgt` | 北向资金流向 |
| `get_company_news` | `stock_news` | 公司新闻 |
| `get_financial_statements` | `stock_financial` | 财务指标 |
| DOCX skill | MD native | 报告输出格式 |

### valuation_calc 工具速查

| 用途 | calc_type | 关键参数 |
|------|-----------|---------|
| 历史增速 | `cagr` | begin_value, end_value, years |
| 权益成本 | `capm` | risk_free_rate, beta, market_return |
| 加权资本成本 | `wacc` | equity, debt, cost_of_equity, cost_of_debt, tax_rate |
| DCF 估值 | `dcf` | cash_flows[], wacc_rate, terminal_growth_rate, net_debt |
| 百分位排名 | `percentile_rank` | values[], value |

## 重要提示

- 研究报告应基于真实数据生成，所有估值数字需来自 `stock_quote`、`stock_financial` 等工具的实际返回值
- 如果某些数据获取失败，在对应章节标注"（数据暂缺）"，不要编造数据
- **所有财务计算（DCF、WACC、CAPM、CAGR 等）必须使用 `valuation_calc` 工具，禁止心算**
- DCF 估值需注明核心假设，体现投行分析的严谨性
- 最终报告应保存到 workspace，使 ContextBuilder 能在后续对话中加载为长期记忆
