"""AKShare-based stock data scraping tools.

Provides real-time quotes, K-line data, financial indicators,
sector flow, and multi-source news for Chinese A-shares, Hong Kong, and US markets.

Includes BaoStock as fallback data source for A-share K-line and quote data.
"""

from __future__ import annotations

import os
from typing import Any

# Disable proxy for stock data APIs (they don't need proxy)
os.environ.setdefault("NO_PROXY", "eastmoney.com,baostock.com")

try:
    import akshare as ak
except ImportError:
    ak = None  # type: ignore

try:
    import baostock as bs
except ImportError:
    bs = None  # type: ignore

import pandas as pd
from fincat.chart_builder import store_chart_data


def _baostock_login():
    """Login to BaoStock if available."""
    if bs is not None:
        try:
            bs.login()
            return True
        except Exception:
            return False
    return False


def _baostock_logout():
    """Logout from BaoStock."""
    if bs is not None:
        try:
            bs.logout()
        except Exception:
            pass


def _symbol_to_baostock(symbol: str) -> str:
    """Convert symbol to BaoStock format (sh.600519 or sz.000858)."""
    sym = symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if sym.startswith("6") or sym.startswith("9"):
        return f"sh.{sym}"
    else:
        return f"sz.{sym}"

from fincat.agent.tools.base import Tool, tool_parameters


# ---------------------------------------------------------------------------
# StockQuoteTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Stock symbols list, e.g. ['600519.SH', '000858.SZ', 'AAPL']",
        },
        "market": {
            "type": "string",
            "enum": ["a", "hk", "us"],
            "description": "Market type: a (A股), hk (港股), us (美股)",
            "default": "a",
        },
    },
    "required": ["symbols"],
})
class StockQuoteTool(Tool):
    """Get real-time quotes for Chinese A-shares, HK, and US stocks."""

    name = "stock_quote"
    description = """Get real-time quotes for multiple stocks.

Supports: A-shares (600519.SH), HK (0700.HK), US (AAPL).

Returns: symbol, price, change, change_pct, volume, turnover, high, low, open, close, market_cap."""

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, symbols: list[str], market: str = "a", **kwargs: Any) -> str:
        if ak is None and bs is None:
            return "(neither akshare nor baostock installed)"

        # Try akshare first
        if ak is not None:
            try:
                if market == "a":
                    df = ak.stock_zh_a_spot_em()
                    code_set = {s.replace(".SH", "").replace(".SZ", "") for s in symbols}
                    df = df[df["代码"].isin(code_set)]
                    if not df.empty:
                        chart_df = pd.DataFrame({
                            "代码": df["代码"],
                            "名称": df.get("名称", ""),
                            "最新价": pd.to_numeric(df.get("最新价", 0), errors="coerce"),
                            "涨跌幅": pd.to_numeric(df.get("涨跌幅", 0), errors="coerce"),
                            "成交量": pd.to_numeric(df.get("成交量", 0), errors="coerce"),
                        })
                        store_chart_data("quote", chart_df, name_col="名称", change_col="涨跌幅")
                    lines = ["## A股实时行情\n"]
                    for _, row in df.iterrows():
                        lines.append(
                            f"**{row['代码']}** {row.get('名称', '')}\n"
                            f"  现价: {row.get('最新价', 'N/A')} | 涨跌: {row.get('涨跌幅', 'N/A')}%\n"
                            f"  今开: {row.get('今开', 'N/A')} | 最高: {row.get('最高', 'N/A')} | 最低: {row.get('最低', 'N/A')}\n"
                            f"  成交量: {row.get('成交量', 'N/A')} | 成交额: {row.get('成交额', 'N/A')}\n"
                        )
                    return "\n".join(lines) if len(lines) > 1 else "No data found for specified symbols."

                elif market == "hk":
                    df = ak.stock_hk_spot_em()
                    code_set = {s.replace(".HK", "") for s in symbols}
                    df = df[df["代码"].isin(code_set)]
                    lines = ["## 港股实时行情\n"]
                    for _, row in df.iterrows():
                        lines.append(
                            f"**{row['代码']}** {row.get('名称', '')}\n"
                            f"  现价: {row.get('最新价', 'N/A')} | 涨跌: {row.get('涨跌幅', 'N/A')}%\n"
                        )
                    return "\n".join(lines) if len(lines) > 1 else "No data found."

                elif market == "us":
                    df = ak.stock_us_spot_em()
                    df = df[df["代码"].isin(symbols)]
                    lines = ["## 美股实时行情\n"]
                    for _, row in df.iterrows():
                        lines.append(
                            f"**{row['代码']}** {row.get('名称', '')}\n"
                            f"  现价: {row.get('最新价', 'N/A')} | 涨跌: {row.get('涨跌幅', 'N/A')}%\n"
                        )
                    return "\n".join(lines) if len(lines) > 1 else "No data found."

            except Exception:
                # Fall through to BaoStock for A-shares
                pass

        # Fallback: BaoStock for A-shares
        if bs is not None and market == "a":
            try:
                from datetime import datetime, timedelta
                _baostock_login()
                lines = ["## A股行情 [BaoStock]\n"]
                for symbol in symbols:
                    bs_sym = _symbol_to_baostock(symbol)
                    today = datetime.now().strftime("%Y-%m-%d")
                    yesterday = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                    rs = bs.query_history_k_data_plus(
                        bs_sym,
                        "date,open,high,low,close,volume,amount,pctChg",
                        start_date=yesterday,
                        end_date=today,
                        frequency="d",
                    )
                    data = []
                    while rs.next():
                        data.append(rs.get_row_data())
                    if data:
                        row = data[-1]  # Latest row
                        lines.append(
                            f"**{symbol}**\n"
                            f"  日期: {row[0]} | 收盘: {row[4]} | 涨跌: {row[7]}%\n"
                            f"  开: {row[1]} | 高: {row[2]} | 低: {row[3]}\n"
                            f"  成交量: {row[5]} | 成交额: {row[6]}\n"
                        )
                    else:
                        lines.append(f"**{symbol}**: No data found\n")
                _baostock_logout()
                return "\n".join(lines) if len(lines) > 1 else "No data found."
            except Exception as e:
                _baostock_logout()
                return f"Failed to get quotes (both sources failed): {e}"

        return "No data source available for this market"


# ---------------------------------------------------------------------------
# StockKlineTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "Stock symbol, e.g. 600519.SH"},
        "period": {
            "type": "string",
            "enum": ["daily", "weekly", "monthly"],
            "default": "daily",
        },
        "start_date": {"type": "string", "description": "Start date YYYYMMDD"},
        "end_date": {"type": "string", "description": "End date YYYYMMDD"},
        "adjust": {"type": "string", "enum": ["qfq", "hfq", ""], "default": "qfq"},
    },
    "required": ["symbol"],
})
class StockKlineTool(Tool):
    """Get K-line (OHLCV) data for stocks."""

    name = "stock_kline"
    description = """Get K-line (candlestick) data for a stock.

Args:
- symbol: Stock code (e.g., "600519.SH", "000858.SZ")
- period: "daily", "weekly", "monthly" (default: "daily")
- start_date: Start date YYYYMMDD (optional, default: 20230101)
- end_date: End date YYYYMMDD (optional, default: today)
- adjust: "qfq" (default), "hfq", or "" for no adjustment

Returns: Date, Open, High, Low, Close, Volume, Turnover, Change%."""

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "20230101",
        end_date: str = "",
        adjust: str = "qfq",
        **kwargs: Any,
    ) -> str:
        if ak is None and bs is None:
            return "(neither akshare nor baostock installed)"

        # Try akshare first
        if ak is not None:
            try:
                sym = symbol.replace(".SH", "").replace(".SZ", "")
                period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
                df = ak.stock_zh_a_hist(
                    symbol=sym,
                    period=period_map.get(period, "daily"),
                    start_date=start_date,
                    end_date=end_date or "",
                    adjust=adjust,
                )
                df = df.tail(30)
                # Store structured data for chart rendering
                chart_df = pd.DataFrame({
                    "日期": df["日期"].astype(str),
                    "开": pd.to_numeric(df["开盘"], errors="coerce"),
                    "高": pd.to_numeric(df["最高"], errors="coerce"),
                    "低": pd.to_numeric(df["最低"], errors="coerce"),
                    "收": pd.to_numeric(df["收盘"], errors="coerce"),
                    "量": pd.to_numeric(df["成交量"], errors="coerce"),
                })
                store_chart_data("kline", chart_df,
                                 date_col="日期", open_col="开", high_col="高",
                                 low_col="低", close_col="收", vol_col="量")
                lines = [f"## {symbol} K线 ({period})\n"]
                lines.append("| 日期 | 开 | 高 | 低 | 收 | 量 | 额 | 涨跌幅 |")
                lines.append("|---|---|---|---|---|---|---|---|")
                for _, row in df.iterrows():
                    lines.append(
                        f"| {row['日期']} | {row['开盘']} | {row['最高']} | {row['最低']} | "
                        f"{row['收盘']} | {row['成交量']} | {row['成交额']} | {row['涨跌幅']}% |"
                    )
                return "\n".join(lines)
            except Exception as e:
                # Fall through to BaoStock
                pass

        # Fallback: BaoStock
        if bs is not None:
            try:
                bs_sym = _symbol_to_baostock(symbol)
                freq_map = {"daily": "d", "weekly": "w", "monthly": "m"}
                freq = freq_map.get(period, "d")

                # Format dates for BaoStock (YYYY-MM-DD)
                sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else "2023-01-01"
                ed = ""
                if end_date and len(end_date) == 8:
                    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

                _baostock_login()
                rs = bs.query_history_k_data_plus(
                    bs_sym,
                    "date,open,high,low,close,volume,amount,pctChg",
                    start_date=sd,
                    end_date=ed,
                    frequency=freq,
                    adjustflag="2" if adjust == "qfq" else ("1" if adjust == "hfq" else "3"),
                )

                data = []
                while rs.next():
                    data.append(rs.get_row_data())
                _baostock_logout()

                if not data:
                    return f"No K-line data found for {symbol}"

                data = data[-30:]  # Last 30 rows
                # Store structured data for chart rendering
                chart_df = pd.DataFrame(data, columns=["日期", "开", "高", "低", "收", "量", "额", "涨跌幅"])
                for col in ["开", "高", "低", "收", "量", "额", "涨跌幅"]:
                    chart_df[col] = pd.to_numeric(chart_df[col], errors="coerce")
                store_chart_data("kline", chart_df,
                                 date_col="日期", open_col="开", high_col="高",
                                 low_col="低", close_col="收", vol_col="量")
                lines = [f"## {symbol} K线 ({period}) [BaoStock]\n"]
                lines.append("| 日期 | 开 | 高 | 低 | 收 | 量 | 额 | 涨跌幅 |")
                lines.append("|---|---|---|---|---|---|---|---|")
                for row in data:
                    lines.append(
                        f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | "
                        f"{row[4]} | {row[5]} | {row[6]} | {row[7]}% |"
                    )
                return "\n".join(lines)
            except Exception as e:
                _baostock_logout()
                return f"Failed to get K-line data (both sources failed): {e}"

        return "No data source available"


# ---------------------------------------------------------------------------
# StockFinancialTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "Stock symbol, e.g. 600519.SH"},
    },
    "required": ["symbol"],
})
class StockFinancialTool(Tool):
    """Get financial indicators (PE, ROE, gross margin, etc.)."""

    name = "stock_financial"
    description = """Get financial indicators for a stock.

Includes: ROE, gross margin, net margin, EPS, book value per share, etc.

Args:
- symbol: Stock code (e.g., "600519.SH")"""

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, symbol: str, **kwargs: Any) -> str:
        if ak is None:
            return "(akshare not installed: pip install akshare)"

        try:
            sym = symbol.replace(".SH", "").replace(".SZ", "")
            df = ak.stock_financial_analysis_indicator(symbol=sym)
            df = df.head(4)
            lines = [f"## {symbol} 财务指标\n"]
            cols = [
                "日期",
                "净资产收益率(%)",
                "销售毛利率(%)",
                "销售净利率(%)",
                "每股收益",
                "每股净资产",
            ]
            available = [c for c in cols if c in df.columns]
            header = "| " + " | ".join(available) + " |"
            lines.append(header)
            lines.append("|" + "|".join(["---"] * len(available)) + "|")
            for _, row in df.iterrows():
                vals = []
                for c in available:
                    v = row.get(c, "")
                    vals.append(str(v) if v is not None else "")
                lines.append("| " + " | ".join(vals) + " |")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to get financial data: {e}"


# ---------------------------------------------------------------------------
# StockHsgtTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "direction": {
            "type": "string",
            "enum": ["north", "south"],
            "description": "north=北向(沪深港通), south=南向(港股通)",
        },
        "start_date": {"type": "string", "description": "Start date YYYYMMDD"},
        "end_date": {"type": "string", "description": "End date YYYYMMDD"},
    },
    "required": ["direction"],
})
class StockHsgtTool(Tool):
    """Get northbound/southbound capital flow (HSGT)."""

    name = "stock_hsgt"
    description = """Get northbound (HK->CN) and southbound (CN->HK) capital flow data.

Use: "north" for northbound flow (北向), "south" for southbound flow (南向)."""

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, direction: str, start_date: str = "", end_date: str = "", **kwargs: Any) -> str:
        if ak is None:
            return "(akshare not installed: pip install akshare)"

        try:
            if direction == "north":
                df_sh = ak.stock_hsgt_north_net_flow_in(em="sh")
                df_sz = ak.stock_hsgt_north_net_flow_in(em="sz")
                # Merge: take top 10 by date
                df = df_sh.head(10)
                lines = ["## 北向资金流向（近10日）\n"]
            else:
                df = ak.stock_hsgt_south_net_flow_in(em="hk")
                df = df.head(10)
                lines = ["## 南向资金流向（近10日）\n"]

            date_col = "日期" if "日期" in df.columns else df.columns[0]
            net_col = "净流入" if "净流入" in df.columns else df.columns[1]
            amount_col = "成交额" if "成交额" in df.columns else df.columns[2] if len(df.columns) > 2 else ""

            lines.append("| 日期 | 净流入 | 成交额 |")
            lines.append("|---|---|---|")
            for _, row in df.iterrows():
                dt = str(row.get(date_col, ""))
                net = str(row.get(net_col, ""))
                amt = str(row.get(amount_col, "")) if amount_col else ""
                lines.append(f"| {dt} | {net} | {amt} |")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to get HSGT data: {e}"


# ---------------------------------------------------------------------------
# StockBlockTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["industry", "concept"],
            "default": "industry",
        },
        "top_n": {"type": "integer", "default": 10},
        "sort_by": {"type": "string", "enum": ["change", "volume"], "default": "change"},
    },
})
class StockBlockTool(Tool):
    """Get sector/industry board performance."""

    name = "stock_block"
    description = """Get sector/industry board heat and performance.

Args:
- type: "industry" (行业) or "concept" (概念)
- top_n: Number of top/bottom boards to return (default: 10)
- sort_by: "change" (涨跌幅) or "volume" (成交量)"""

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, type: str = "industry", top_n: int = 10, sort_by: str = "change", **kwargs: Any) -> str:
        if ak is None:
            return "(akshare not installed: pip install akshare)"

        try:
            if type == "industry":
                df = ak.stock_board_industry_name_em()
            else:
                df = ak.stock_board_concept_name_em()

            col = "涨跌幅" if sort_by == "change" else "成交量"
            if col not in df.columns:
                col = df.columns[1]
            df = df.sort_values(col, ascending=False).head(top_n)

            lines = [f"## {'行业' if type == 'industry' else '概念'}板块 (Top {top_n} by {col})\n"]
            # Find available columns
            name_col = "名称" if "名称" in df.columns else df.columns[0]
            change_col = "涨跌幅" if "涨跌幅" in df.columns else df.columns[1]
            vol_col = "成交量" if "成交量" in df.columns else (df.columns[2] if len(df.columns) > 2 else "")
            amt_col = "成交额" if "成交额" in df.columns else ""

            header = "| 板块 | 涨跌幅 | 成交量"
            if amt_col:
                header += " | 成交额"
            header += " |"
            lines.append(header)
            lines.append("|" + "|".join(["---"] * (3 if not amt_col else 4)) + "|")

            for _, row in df.iterrows():
                name = str(row.get(name_col, ""))
                change = str(row.get(change_col, ""))
                vol = str(row.get(vol_col, ""))
                amt = str(row.get(amt_col, "")) if amt_col else ""
                if amt:
                    lines.append(f"| {name} | {change}% | {vol} | {amt} |")
                else:
                    lines.append(f"| {name} | {change}% | {vol} |")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to get block data: {e}"


# ---------------------------------------------------------------------------
# StockIndicatorTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "Stock symbol, e.g. 600519.SH"},
        "period": {
            "type": "string",
            "enum": ["daily", "weekly", "monthly"],
            "default": "daily",
        },
        "start_date": {"type": "string", "description": "Start date YYYYMMDD"},
    },
    "required": ["symbol"],
})
class StockIndicatorTool(Tool):
    """Calculate technical indicators: MA, MACD, RSI, KDJ, Bollinger Bands."""

    name = "stock_indicator"
    description = """Calculate technical indicators and analyze technical state for a stock.

Calculates:
- MA: MA5, MA10, MA20, MA60 (moving averages)
- MACD: DIF, DEA, MACD histogram
- RSI: Relative Strength Index (14 periods)
- KDJ: K, D, J values
- Bollinger Bands: Upper, Middle, Lower bands

Technical State Analysis:
- Golden Cross / Death Cross (MA10 vs MA20)
- Overbought / Oversold signals (RSI > 70 / < 30)
- MACD zero-line crossover
- KDJ overbought/oversold (J > 80 / < 20)
- Bollinger Bands position
- Trend direction: Uptrend / Downtrend / Sideways

Args:
- symbol: Stock code (e.g., "600519.SH")
- period: K-line period: "daily", "weekly", "monthly" (default: "daily")
- start_date: Start date YYYYMMDD (default: 60 days ago)"""

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "",
        **kwargs: Any,
    ) -> str:
        if ak is None:
            return "(akshare not installed: pip install akshare)"

        try:
            import pandas as pd

            sym = symbol.replace(".SH", "").replace(".SZ", "")
            period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
            df = ak.stock_zh_a_hist(
                symbol=sym,
                period=period_map.get(period, "daily"),
                start_date=start_date or "20250101",
                end_date="",
                adjust="qfq",
            )
            df = df.tail(60)

            close = pd.to_numeric(df["收盘"], errors="coerce")
            current_price = close.iloc[-1]

            # MA
            ma5 = close.rolling(5).mean()
            ma10 = close.rolling(10).mean()
            ma20 = close.rolling(20).mean()
            ma60 = close.rolling(60).mean()

            # MACD
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            macd_hist = (dif - dea) * 2

            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta.where(delta < 0, 0.0))
            avg_gain = gain.rolling(14).mean()
            avg_loss = loss.rolling(14).mean()
            rs = avg_gain / (avg_loss + 1e-9)
            rsi14 = 100 - (100 / (1 + rs))

            # KDJ
            low14 = df["最低"].rolling(9).min()
            high14 = df["最高"].rolling(9).max()
            rsv = (close - low14) / (high14 - low14 + 1e-9) * 100
            k = rsv.ewm(com=2, adjust=False).mean()
            d = k.ewm(com=2, adjust=False).mean()
            j = 3 * k - 2 * d

            # Bollinger Bands
            bbm = close.rolling(20).mean()
            bbb = close.rolling(20).std()
            bb_upper = bbm + 2 * bbb
            bb_lower = bbm - 2 * bbb

            # Technical state
            state_parts = []

            ma60_val = ma60.iloc[-1]
            if current_price > ma60_val:
                trend = "Uptrend (price > MA60)"
            elif current_price < ma60_val:
                trend = "Downtrend (price < MA60)"
            else:
                trend = "Sideways"
            state_parts.append(f"- **Trend**: {trend}")

            ma10_prev, ma20_prev = ma10.iloc[-2], ma20.iloc[-2]
            ma10_curr, ma20_curr = ma10.iloc[-1], ma20.iloc[-1]
            if ma10_prev < ma20_prev and ma10_curr > ma20_curr:
                state_parts.append("- **Signal**: Golden Cross (MA10 crosses above MA20) - Bullish")
            elif ma10_prev > ma20_prev and ma10_curr < ma20_curr:
                state_parts.append("- **Signal**: Death Cross (MA10 crosses below MA20) - Bearish")
            else:
                state_parts.append(f"- **MA Cross**: MA10={ma10_curr:.2f} vs MA20={ma20_curr:.2f}")

            rsi_curr = rsi14.iloc[-1]
            if rsi_curr > 70:
                state_parts.append(f"- **RSI(14)**: {rsi_curr:.1f} - Overbought (>70)")
            elif rsi_curr < 30:
                state_parts.append(f"- **RSI(14)**: {rsi_curr:.1f} - Oversold (<30)")
            else:
                state_parts.append(f"- **RSI(14)**: {rsi_curr:.1f} - Neutral (30-70)")

            dif_prev, dea_prev = dif.iloc[-2], dea.iloc[-2]
            dif_curr, dea_curr = dif.iloc[-1], dea.iloc[-1]
            macd_curr = macd_hist.iloc[-1]
            if dif_prev < dea_prev and dif_curr > dea_curr:
                state_parts.append(f"- **MACD**: DIF crosses above DEA - Bullish signal")
            elif dif_prev > dea_prev and dif_curr < dea_curr:
                state_parts.append(f"- **MACD**: DIF crosses below DEA - Bearish signal")
            elif macd_curr > 0:
                state_parts.append(f"- **MACD**: DIF={dif_curr:.3f}, DEA={dea_curr:.3f}, Hist={macd_curr:.3f} - Above Zero")
            else:
                state_parts.append(f"- **MACD**: DIF={dif_curr:.3f}, DEA={dea_curr:.3f}, Hist={macd_curr:.3f} - Below Zero")

            k_curr, d_curr, j_curr = k.iloc[-1], d.iloc[-1], j.iloc[-1]
            if j_curr > 80:
                state_parts.append(f"- **KDJ**: K={k_curr:.1f}, D={d_curr:.1f}, J={j_curr:.1f} - Overbought")
            elif j_curr < 20:
                state_parts.append(f"- **KDJ**: K={k_curr:.1f}, D={d_curr:.1f}, J={j_curr:.1f} - Oversold")
            else:
                state_parts.append(f"- **KDJ**: K={k_curr:.1f}, D={d_curr:.1f}, J={j_curr:.1f} - Neutral")

            bb_pos = (current_price - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1] + 1e-9) * 100
            state_parts.append(
                f"- **Bollinger**: Price at {bb_pos:.1f}% "
                f"(Upper={bb_upper.iloc[-1]:.2f}, Mid={bbm.iloc[-1]:.2f}, Lower={bb_lower.iloc[-1]:.2f})"
            )

            # Format output
            lines = [f"## {symbol} 技术指标 ({period})\n"]
            lines.append(f"**Current Price**: {current_price:.2f}\n")

            lines.append("### MA (Moving Averages)")
            lines.append("| MA5 | MA10 | MA20 | MA60 |")
            lines.append("|---|---|---|---|")
            lines.append(f"| {ma5.iloc[-1]:.2f} | {ma10.iloc[-1]:.2f} | {ma20.iloc[-1]:.2f} | {ma60.iloc[-1]:.2f} |")

            lines.append("\n### MACD")
            lines.append("| DIF | DEA | MACD Histogram |")
            lines.append("|---|---|---|")
            lines.append(f"| {dif.iloc[-1]:.4f} | {dea.iloc[-1]:.4f} | {macd_hist.iloc[-1]:.4f} |")

            lines.append("\n### RSI")
            rsi_status = "Overbought" if rsi_curr > 70 else "Oversold" if rsi_curr < 30 else "Neutral"
            lines.append("| RSI(14) | Status |")
            lines.append("|---|---|")
            lines.append(f"| {rsi_curr:.2f} | {rsi_status} |")

            lines.append("\n### KDJ")
            lines.append("| K | D | J |")
            lines.append("|---|---|---|")
            lines.append(f"| {k.iloc[-1]:.2f} | {d.iloc[-1]:.2f} | {j.iloc[-1]:.2f} |")

            lines.append("\n### Bollinger Bands")
            bb_width = (bb_upper.iloc[-1] - bb_lower.iloc[-1]) / bbm.iloc[-1] * 100
            lines.append("| Upper | Middle | Lower | Band Width |")
            lines.append("|---|---|---|---|")
            lines.append(f"| {bb_upper.iloc[-1]:.2f} | {bbm.iloc[-1]:.2f} | {bb_lower.iloc[-1]:.2f} | {bb_width:.2f}% |")

            lines.append("\n### Technical State Analysis")
            lines.extend(state_parts)

            return "\n".join(lines)

        except Exception as e:
            import traceback
            return f"Failed to calculate indicators: {e}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# StockNewsTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "symbol": {
            "type": "string",
            "description": "Stock code (e.g., 600519.SH) or empty for market news",
        },
        "max_results": {
            "type": "integer",
            "description": "Max results per source (default: 5)",
            "default": 5,
        },
    },
})
class StockNewsTool(Tool):
    """Multi-source news: akshare first, then web search fallback.

    Merges structured akshare news with broader web search results.
    """

    name = "stock_news"
    description = """Get comprehensive news for a stock or the market.

Tries multiple sources in order:
1. AKShare official news (stock-specific, structured)
2. Web search (broader coverage from news sites, forums, etc.)

Args:
- symbol: Stock code (e.g., "600519.SH") or empty for market-wide news
- max_results: Maximum news items per source (default: 5)

Returns merged, time-sorted news feed with source attribution."""

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, symbol: str = "", max_results: int = 5, **kwargs: Any) -> str:
        if ak is None:
            return "(akshare not installed: pip install akshare)"

        parts: list[str] = []

        # Source 1: AKShare stock news
        try:
            if symbol:
                sym = symbol.replace(".SH", "").replace(".SZ", "")
                df = ak.stock_news_em(symbol=sym)
                df = df.head(max_results)
                lines = [f"## {symbol} 新闻 (AKShare)\n"]
                for _, row in df.iterrows():
                    title = row.get("标题", "")
                    dt = row.get("发布时间", "")
                    url = row.get("链接", "")
                    if title:
                        lines.append(f"- [{title}]({url}) - {dt}")
                akshare_news = "\n".join(lines)
            else:
                # Market-wide: use stock info as proxy
                df = ak.stock_info_em()
                lines = ["## 市场要闻 (AKShare)\n"]
                for _, row in df.head(max_results).iterrows():
                    title = row.get("文章标题", row.get("标题", ""))
                    dt = row.get("发布日期", row.get("日期", ""))
                    if title:
                        lines.append(f"- {title} - {dt}")
                akshare_news = "\n".join(lines)
            parts.append(akshare_news)
        except Exception as e:
            parts.append(f"(AKShare news unavailable: {e})")

        # Source 2: Web search fallback
        try:
            from fincat.agent.tools.web import WebSearchTool

            search_tool = WebSearchTool()
            if symbol:
                query = f"{symbol.replace('.SH', '').replace('.SZ', '')} 股票 新闻"
            else:
                query = "A股 市场 要闻 今日"

            web_result = await search_tool.execute(query=query, count=max_results)
            web_lines = ["\n## 新闻 (Web Search)\n"]
            in_results = False
            for line in web_result.split("\n"):
                stripped = line.strip()
                if not stripped or stripped.startswith("Results for"):
                    continue
                if stripped.startswith(f"{max_results + 1}.") or stripped.startswith("Error:"):
                    break
                web_lines.append(stripped)
                in_results = True
            parts.append("\n".join(web_lines))
        except Exception as e:
            parts.append(f"(Web search unavailable: {e})")

        combined = "\n".join(parts).strip()
        return combined if combined else "No news found from any source."
