---
name: stock-analysis
description: "个股综合技术分析：实时行情、K线数据、技术指标（MA/MACD/RSI/KDJ/布林带）、板块对比。当用户说'分析股票'、'帮我看看'、'技术分析'、'什么位置'时触发。"
---

# Stock Analysis

对给定股票标的进行综合技术分析。需询问用户股票代码或名称。

## 工作流

1. **实时行情**：`stock_quote(symbols=["XXXXXX"], market="a")` 查最新价、涨跌幅、成交量
2. **K线数据**：`stock_kline(symbol="XXXXXX", period="daily", start_date="YYYYMMDD")` 查近60日K线
3. **技术指标**：`stock_indicator(symbol="XXXXXX")` 获取 MA/MACD/RSI/KDJ/布林带综合分析
4. **板块对比**：用 `stock_block(type="industry")` 查个股所在板块今日表现

## 输出格式

---
**{股票名称} ({代码}) 技术分析**

**当前状态**：{价格} ({涨跌%}) | {成交量}万手 | {成交额}亿元

**技术指标摘要**：
- 趋势：MA5>{MA10}>{MA20} → {多头/空头/震荡}
- MACD：{金叉/死叉/背离} — DIF={X}, DEA={Y}
- RSI(14)：{数值} — {超买/超卖/中性}
- KDJ: K={K} D={D} J={J} — {状态}
- 布林带：价格处于{BOLL位置}（上轨/中轨/下轨）

**板块位置**：{板块名}，今日{涨跌%}

**综合判断**：{1-2句话结论：支撑位、压力位、趋势判断}
---
