# FinanceCalcTool — 确定性金融计算工具

**日期**: 2026-05-16
**状态**: 已完成

## 背景

fincat 的金融计算能力之前分为两层：
- **已有代码**：技术指标（MA/MACD/RSI/KDJ/布林带）在 `StockIndicatorTool` 中实现
- **LLM 估算**：DCF/WACC/CAPM 等估值计算仅在 skill 文档中描述公式，由 LLM 自行心算，结果不可靠
- **完全缺失**：个人金融场景（复利/年金/贷款/利率转换）

新增 `FinanceCalcTool`，用纯 Python 实现 28 种金融计算，零外部依赖（仅 `math` + `json`），结果精确可复现。

---

## 变更文件

| 文件 | 变更 |
|------|------|
| `fincat/agent/tools/finance_calc.py` | **新建** — 28 种计算的主工具类 |
| `fincat/agent/loop.py` | **修改** — 导入 + 注册 |
| `fincat/templates/TOOLS.md` | **修改** — 新增 finance_calc 文档 |

---

## 28 种计算类型详解

### 一、货币时间价值（TVM）— 9 种

#### 1. `compound_interest` — 复利终值
计算本金在给定利率和期数下的终值，支持每期追加投入。

- **场景**：10 万存 5 年年化 4%，每年底追加 2 万，最终多少？
- **输入**：`pv`(本金), `rate`(每期利率), `periods`(期数), `pmt`(每期追加, 默认 0)
- **公式**：FV = PV×(1+r)^n + PMT×((1+r)^n - 1)/r；rate=0 时退化为 FV = PV + PMT×n
- **输出**：fv, total_interest

#### 2. `annuity_fv` — 年金终值（普通年金）
计算每期固定投入在复利下的终值。

- **场景**：每月定投 3000 元基金，年化 8%，10 年后本息合计？
- **输入**：`pmt`(每期投入), `rate`(每期利率), `periods`(期数)
- **公式**：FV = PMT × ((1+r)^n - 1) / r
- **输出**：fv, total_contributions, total_interest

#### 3. `annuity_pv` — 年金现值（普通年金）
计算未来每期固定领取的现值，用于保险/退休规划定价。

- **场景**：退休后每月领 5000 元领 20 年，现在需要准备多少钱？
- **输入**：`pmt`(每期领取), `rate`(每期利率), `periods`(期数)
- **公式**：PV = PMT × (1 - (1+r)^(-n)) / r
- **输出**：pv, total_contributions

#### 4. `present_value` — 现值
将未来金额折现到当前。

- **场景**：3 年后拿到 50 万，按 6% 折现率现在值多少？
- **输入**：`fv`(终值), `rate`(折现率), `periods`(期数)
- **公式**：PV = FV / (1+r)^n

#### 5. `future_value` — 终值
将当前金额按复利增长到未来。

- **场景**：现在有 20 万，年化 5%，5 年后变多少？
- **输入**：`pv`(现值), `rate`(利率), `periods`(期数)
- **公式**：FV = PV × (1+r)^n

#### 6. `loan_payment` — 等额本息月供
计算等额本息还款方式下的每期固定还款额。

- **场景**：商贷 100 万，利率 4.8%（月利率 0.4%），30 年（360 期），月供多少？
- **输入**：`principal`(贷款额), `rate`(月利率), `periods`(总月数)
- **公式**：PMT = P × r / (1 - (1+r)^(-n))
- **输出**：payment, total_paid, total_interest

#### 7. `amortization_schedule` — 等额本息还款计划表
输出等额本息的逐期还款明细。

- **场景**：100 万房贷完整还款明细，看每期本金/利息/剩余余额
- **输入**：同 loan_payment + `max_periods`(截断行数, 默认 360)
- **输出**：`schedule[]` 每行 {period, payment, principal_part, interest, balance} + summary
- **说明**：periods > max_periods 时截断并标注 truncated

#### 8. `equal_principal_payment` — 等额本金首期月供
计算等额本金还款方式下的首期/末期月供和每期递减额。

- **场景**：100 万房贷选等额本金，首期月供多少？每期递减多少？
- **输入**：`principal`(贷款额), `rate`(月利率), `periods`(总期数)
- **公式**：每期本金 = P/n；第 t 期利息 = (P - P/n×(t-1))×r
- **输出**：first_payment, last_payment, payment_decrease, total_paid, total_interest

#### 9. `equal_principal_schedule` — 等额本金还款计划表
输出等额本金的逐期还款明细，可与等额本息对比总利息差异。

- **场景**：等额本金完整还款明细
- **输入**：同 equal_principal_payment + `max_periods`(截断行数, 默认 360)
- **输出**：`schedule[]` + summary

---

### 二、利率转换（Rate Conversion）— 2 种

#### 10. `convert_annual_to_periodic` — 年利率拆分为周期利率
将名义年利率拆分为月利率/日利率/季利率，支持不同计息天数惯例。

- **场景**：银行说年利率 4.8%，月利率和日利率分别是多少？国内银行用 360 天还是 365 天？
- **输入**：`annual_rate`(名义年利率), `target_period`("month"/"day"/"quarter"/"week"), `day_count_convention`(360/365/366, 默认 360)
- **公式**：月利率 = annual_rate/12；日利率 = annual_rate/day_count
- **输出**：periodic_rate, description（自动中文描述，如"日息万分之五"）

#### 11. `convert_nominal_to_effective` — 名义利率转有效年利率（EAR）
将名义年利率（APR）转为有效年利率（EAR），考虑复利效应。

- **场景**：某理财名义年化 12% 按月复利，实际年化多少？对比另一款名义 11.5% 按日复利
- **输入**：`nominal_rate`(APR), `compounding_frequency`(年复利次数: 4=季, 12=月, 365=日)
- **公式**：EAR = (1 + r/m)^m - 1
- **输出**：effective_annual_rate, difference(EAR - APR)

---

### 三、投资估值（Investment Valuation）— 6 种

#### 12. `wacc` — 加权平均资本成本
计算企业的加权平均资本成本，是 DCF 折现率的核心输入。

- **场景**：茅台市值 2 万亿、负债 500 亿，权益成本 10%、债务成本 4.5%、税率 25%
- **输入**：`equity`(权益市值), `debt`(负债市值), `cost_of_equity`, `cost_of_debt`, `tax_rate`
- **公式**：WACC = E/(D+E)×Ke + D/(D+E)×Kd×(1-T)
- **输出**：wacc, equity_weight, debt_weight, after_tax_cost_of_debt

#### 13. `capm` — 资本资产定价模型
用 CAPM 模型计算权益成本（Ke）。

- **场景**：无风险利率 2.5%、茅台 beta 0.8、市场预期收益 10%，茅台的权益成本？
- **输入**：`risk_free_rate`, `beta`, `market_return`
- **公式**：Ke = Rf + Beta × (Rm - Rf)
- **输出**：cost_of_equity, market_premium

#### 14. `dcf` — 现金流折现估值
完整的 DCF 估值，包含预测期现金流折现 + 永续终值。

- **场景**：茅台未来 5 年 FCF [800, 900, 1000, 1100, 1200] 亿，WACC 9%，永续增长 3%
- **输入**：`cash_flows[]`(各年 FCF), `wacc_rate`, `terminal_growth_rate`, `net_debt`(可选), `shares_outstanding`(可选)
- **公式**：EV = Σ(CF_t/(1+WACC)^t) + TV/(1+WACC)^n，TV = CF_n×(1+g)/(WACC-g)
- **输出**：enterprise_value, terminal_value, pv_of_cash_flows, equity_value, per_share_value
- **校验**：wacc_rate 必须 > terminal_growth_rate

#### 15. `terminal_value` — 永续终值（Gordon 模型）
单独计算永续终值，用于敏感性分析。

- **场景**：最后一年 FCF 1200 亿，永续增长 3%，WACC 9%，终值多少？
- **输入**：`fcf`, `growth_rate`, `wacc_rate`
- **公式**：TV = FCF × (1+g) / (WACC - g)

#### 16. `enterprise_to_equity` — 企业价值 → 股权价值桥接
从企业价值（EV）推导股权价值。

- **场景**：DCF 算出 EV 1.5 万亿，账上现金 2000 亿、有息负债 800 亿
- **输入**：`enterprise_value`, `cash`(可选), `debt`(可选), `minority_interest`(可选)
- **公式**：Equity = EV + Cash - Debt - Minority Interest

#### 17. `cagr` — 复合年增长率
计算两个时间点之间的复合年增长率。

- **场景**：茅台 2019 年营收 888 亿，2024 年 1505 亿，5 年 CAGR？
- **输入**：`begin_value`, `end_value`, `years`
- **公式**：CAGR = (end/begin)^(1/n) - 1
- **输出**：cagr, total_return

---

### 四、资本预算（Capital Budgeting）— 3 种

#### 18. `npv` — 净现值
计算一系列现金流的净现值，判断项目是否值得投资（NPV > 0）。

- **场景**：项目初始投入 500 万，未来 5 年现金流 [150, 180, 200, 220, 250] 万，折现率 10%
- **输入**：`rate`(折现率), `cash_flows[]`(第 0 期为负数)
- **公式**：NPV = Σ(CF_t / (1+r)^t)
- **输出**：npv

#### 19. `irr` — 内部收益率
用 Newton-Raphson 迭代求解使 NPV=0 的折现率。

- **场景**：同一项目 IRR 是多少？和 WACC 比较判断是否值得投
- **输入**：`cash_flows[]`, `guess`(初始猜测, 默认 0.1), `max_iterations`, `tolerance`
- **输出**：irr, iterations, converged
- **校验**：现金流必须有符号变化（至少一个负值和一个正值）

#### 20. `mirr` — 修正内部收益率
IRR 假设再投资收益率等于 IRR 本身（不合理），MIRR 用独立的融资成本和再投资率修正。

- **场景**：修正 IRR 的再投资假设
- **输入**：`cash_flows[]`, `finance_rate`(融资成本), `reinvest_rate`(再投资收益率)
- **公式**：MIRR = (FV_positive / |PV_negative|)^(1/n) - 1

---

### 五、固定收益（Fixed Income）— 3 种

#### 21. `bond_price` — 债券定价
根据市场收益率计算债券价格，支持不同付息频率。

- **场景**：面值 100 的 3 年期债券，票面利率 5%，市场收益率 6%，价格多少？（应低于面值 = 折价）
- **输入**：`face_value`, `coupon_rate`, `periods`, `yield_rate`, `frequency`(付息频率, 默认 1)
- **公式**：P = Σ(C/(1+y/f)^t) + FV/(1+y/f)^n
- **输出**：price, current_yield, coupon_payment

#### 22. `bond_ytm` — 到期收益率（YTM）
已知价格反推到期收益率，Newton-Raphson 迭代求解。

- **场景**：债券价格 95.79，面值 100，票面 5%，3 年期，YTM 是多少？
- **输入**：`price`, `face_value`, `coupon_rate`, `periods`, `frequency`, `guess`
- **输出**：ytm, iterations, converged

#### 23. `bond_duration` — 久期
计算 Macaulay 久期和修正久期，衡量债券价格对利率变动的敏感度。

- **场景**：评估债券的利率风险（久期越长，利率变动时价格波动越大）
- **输入**：`face_value`, `coupon_rate`, `periods`, `yield_rate`, `frequency`
- **公式**：Macaulay Duration = Σ(t × PV(CF_t)) / P；Modified Duration = MacDur / (1+y/f)
- **输出**：macaulay_duration, modified_duration

---

### 六、组合分析（Portfolio Analytics）— 3 种

#### 24. `sharpe_ratio` — 夏普比率
衡量每单位风险的超额收益，用于比较不同投资的风险调整后回报。

- **场景**：基金 A 收益 15% 波动率 20%，基金 B 收益 12% 波动率 10%，无风险利率 3%，哪个更好？
- **输入**：`portfolio_return`, `risk_free_rate`, `portfolio_std_dev`
- **公式**：Sharpe = (Rp - Rf) / σ
- **输出**：sharpe_ratio, excess_return

#### 25. `portfolio_return` — 组合预期收益
计算加权组合的预期收益率。

- **场景**：60% 配股票（预期 10%）+ 40% 配债券（预期 4%）
- **输入**：`weights[]`, `returns[]`
- **公式**：Rp = Σ(w_i × R_i)

#### 26. `portfolio_volatility` — 组合波动率
从协方差矩阵或标准差+相关系数矩阵计算组合波动率。

- **场景**：股票波动率 20%、债券 5%、相关系数 0.2，60/40 组合波动率？
- **输入**：方式 A `weights[]`, `covariance_matrix[][]`；方式 B `weights[]`, `std_devs[]`, `correlation_matrix[][]`
- **公式**：σ_p = √(w' × Cov × w)

---

### 七、统计/比率（Statistics & Ratios）— 2 种

#### 27. `percentile_rank` — 百分位排名
计算某个值在分布中的百分位排名。

- **场景**：茅台 PE 25x，行业 PE 分布 [12, 15, 18, 20, 22, 25, 30, 35, 40, 50]，茅台处于什么分位？
- **输入**：`values[]`(分布数据), `value`(待排名值)
- **输出**：percentile_rank(0-100), rank, count

#### 28. `ratio_analysis` — 财务比率分析
计算流动比率、速动比率、负债权益比、现金比率，含自动健康判定。

- **场景**：公司流动资产 500 亿、流动负债 300 亿、存货 100 亿、总负债 800 亿、净资产 1200 亿
- **输入**：`current_assets`, `current_liabilities`, `inventory`(可选), `total_debt`(可选), `total_equity`(可选), `cash`(可选)
- **输出**：current_ratio, quick_ratio, debt_to_equity, cash_ratio, current_ratio_judgment
- **判定规则**：流动比率 ≥2 良好、1~2 一般、<1 危险

---

## 技术设计

### 架构
- 单工具类 + `calc_type` 分发器（`getattr` 反射调用 `_calc_{type}`）
- Flat schema — 所有参数平铺，仅 `calc_type` required
- 零外部依赖 — 仅 `math` + `json`
- Newton-Raphson 自实现 — 用于 IRR 和 YTM 求解

### 边界处理
- rate=0 时年金/贷款：退化为线性公式
- terminal_value/dcf：校验 wacc > growth_rate
- irr：校验现金流有符号变化，不收敛时返回 `converged: false`
- amortization_schedule：通过 `max_periods` 截断（默认 360）
- portfolio_volatility：校验权重和 ≈ 1.0，矩阵维度匹配

---

## 2026-05-16 拆分重构

### 背景

单工具 28 calc_type + 58 平铺参数导致 schema 过大（~1,596 tokens），LLM 在参数选择上存在噪声干扰。拆分为 6 个独立工具，每个工具参数更少、语义更清晰。

### 删除的 11 种 calc_type

| 删除项 | 原因 |
|--------|------|
| present_value | DCF 内部已实现 |
| future_value | compound_interest(pmt=0) 等价 |
| convert_annual_to_periodic | 月利率=年/12，LLM 直接算 |
| convert_nominal_to_effective | EAR=(1+r/m)^m-1，LLM 直接算 |
| terminal_value | DCF 内部已实现 |
| enterprise_to_equity | DCF 返回 equity_value |
| mirr | IRR 够用 |
| bond_duration | 低频专业场景 |
| sharpe_ratio | (Rp-Rf)/σ，LLM 直接算 |
| portfolio_return | Σ(w×r)，LLM 直接算 |
| portfolio_volatility | 需要协方差矩阵，输入构造困难 |

### 拆分后文件

| 文件 | calc_types | 参数数 |
|------|-----------|--------|
| `_finance_utils.py` | 共享 Newton-Raphson | — |
| `valuation_calc.py` | capm, wacc, dcf, cagr, percentile_rank | 19 |
| `loan_calc.py` | loan_payment, amortization_schedule, equal_principal_payment/schedule | 5 |
| `tvm_calc.py` | compound_interest, annuity_fv, annuity_pv | 5 |
| `bond_calc.py` | bond_price, bond_ytm | 8 |
| `budgeting_calc.py` | irr, npv | 6 |
| `ratio_calc.py` | ratio_analysis | 6 |

### 拆分前后对比

| 指标 | 拆分前 | 拆分后 |
|------|-------|-------|
| 工具数 | 1 | 6 |
| calc_types | 28 | 17 |
| 总参数数 | 58 | 49 |
| 最大单工具参数 | 58 | 19 |
| Schema 总 tokens | ~1,596 | ~1,100 |
