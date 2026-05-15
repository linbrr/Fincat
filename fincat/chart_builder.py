"""Build Plotly charts from akshare tool Markdown output — modern clean style."""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Structured data store: tools deposit DataFrames here ──────────────
_chart_data: dict[str, Any] = {}


def store_chart_data(chart_type: str, df: pd.DataFrame, **meta) -> None:
    """Store structured data from a tool for direct chart rendering."""
    _chart_data[chart_type] = {"df": df, "ts": datetime.now().isoformat(), **meta}


def pop_chart_data(chart_type: str) -> dict[str, Any] | None:
    """Retrieve and remove stored chart data. Returns None if not available."""
    return _chart_data.pop(chart_type, None)

# ── Modern clean theme ────────────────────────────────────────────────
_BG = "#f8fafc"
_PAPER = "#ffffff"
_GRID = "rgba(0,0,0,0.06)"
_FONT = "#334155"
_RED = "#ef4444"   # 涨
_GREEN = "#22c55e" # 跌
_ACCENT = "#3b82f6"


def _terminal_layout(**overrides) -> dict:
    """Return base layout dict with modern clean styling."""
    base = dict(
        template="plotly_white",
        paper_bgcolor=_PAPER,
        plot_bgcolor=_BG,
        font=dict(color=_FONT, size=11, family="system-ui, sans-serif"),
        xaxis=dict(gridcolor=_GRID, zeroline=False, linecolor=_GRID),
        yaxis=dict(gridcolor=_GRID, zeroline=False, linecolor=_GRID),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        margin=dict(l=40, r=16, t=36, b=24),
    )
    base.update(overrides)
    return base


def fig_to_png_bytes(fig: go.Figure, width: int = 400, height: int = 300) -> bytes:
    """Convert a Plotly figure to PNG bytes for sidebar display."""
    fig.update_layout(width=width, height=height)
    return fig.to_image(format="png", scale=2, width=width, height=height)


def _parse_md_table(content: str) -> pd.DataFrame | None:
    """Parse a Markdown table into a DataFrame. Returns None if not found."""
    lines = content.strip().split("\n")
    table_lines = [l.strip() for l in lines if l.strip().startswith("|")]
    if len(table_lines) < 3:
        return None

    headers = [h.strip() for h in table_lines[0].split("|") if h.strip()]
    rows = []
    for line in table_lines[2:]:  # skip header + separator
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) == len(headers):
            rows.append(cells)
    if not rows:
        return None
    return pd.DataFrame(rows, columns=headers)


def _to_numeric(series: pd.Series) -> pd.Series:
    """Strip % and convert to numeric."""
    return pd.to_numeric(series.astype(str).str.replace("%", ""), errors="coerce")


def sidebar_figure(fig: go.Figure) -> go.Figure:
    """Adapt a figure for sidebar display (narrow, compact)."""
    fig.update_layout(
        height=280,
        margin=dict(l=8, r=8, t=32, b=16),
        font=dict(size=10),
        showlegend=False,
    )
    return fig


def build_chart(content: str) -> go.Figure | None:
    """Build a Plotly figure. Prefers structured data from tools; falls back to markdown parsing."""
    fig = None

    # Priority 1: Use structured data deposited by tools (reliable, no parsing needed)
    for chart_type in ("kline", "quote", "hsgt", "indicator", "block", "financial", "pie", "trend"):
        data = pop_chart_data(chart_type)
        if data is not None:
            builder = _STRUCTURED_BUILDERS.get(chart_type)
            if builder:
                fig = builder(data)
            if fig:
                break

    # Priority 2: Parse from markdown content (fallback)
    if not fig and "|" in content:
        if "K线" in content or "k线" in content.lower():
            fig = _build_kline_chart(content)

        if not fig and ("北向资金" in content or "南向资金" in content):
            fig = _build_hsgt_chart(content)

        if not fig and "技术指标" in content:
            fig = _build_indicator_chart(content)

        if not fig and ("实时行情" in content or "现价" in content):
            fig = _build_quote_chart(content)

        if not fig and "板块" in content and ("涨跌幅" in content or "成交量" in content):
            fig = _build_block_chart(content)

        if not fig and ("财务指标" in content or "净资产收益率" in content):
            fig = _build_financial_chart(content)

        if not fig and ("占比" in content or "分布" in content or "比例" in content):
            fig = _build_pie_chart(content)

        if not fig and ("趋势" in content or "走势" in content):
            fig = _build_trend_chart(content)

        # Fallback: detect by table column headers
        if not fig:
            df = _parse_md_table(content)
            if df is not None and len(df.columns) >= 2:
                cols = [str(c).lower() for c in df.columns]
                if any("日期" in c for c in cols) and any("收" in c for c in cols):
                    fig = _build_kline_chart(content)
                elif any("日期" in c for c in cols) and any("净流入" in c for c in cols):
                    fig = _build_hsgt_chart(content)

    if fig:
        return sidebar_figure(fig)
    return None


# ── Chart builders ────────────────────────────────────────────────────

def _build_kline_chart(content: str) -> go.Figure | None:
    df = _parse_md_table(content)
    if df is None or len(df.columns) < 6:
        return None

    cols = list(df.columns)
    date_col = cols[0]
    open_col, high_col, low_col, close_col = cols[1], cols[2], cols[3], cols[4]
    vol_col = cols[5] if len(df.columns) > 5 else None

    for c in [open_col, high_col, low_col, close_col]:
        df[c] = _to_numeric(df[c])
    if vol_col:
        df[vol_col] = _to_numeric(df[vol_col])

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=[0.72, 0.28],
    )

    fig.add_trace(go.Candlestick(
        x=df[date_col], open=df[open_col], high=df[high_col],
        low=df[low_col], close=df[close_col], name="K线",
        increasing_line_color=_RED, decreasing_line_color=_GREEN,
        increasing_fillcolor=_RED, decreasing_fillcolor=_GREEN,
        increasing_line_width=1.5, decreasing_line_width=1.5,
    ), row=1, col=1)

    if vol_col:
        colors = [
            "rgba(239,68,68,0.5)" if c >= o else "rgba(34,197,94,0.5)"
            for c, o in zip(df[close_col], df[open_col])
        ]
        fig.add_trace(go.Bar(
            x=df[date_col], y=df[vol_col], name="成交量",
            marker_color=colors, showlegend=False,
        ), row=2, col=1)

    fig.update_layout(**_terminal_layout(
        height=420,
        xaxis_rangeslider_visible=False,
        xaxis2=dict(gridcolor=_GRID, zeroline=False),
        yaxis=dict(gridcolor=_GRID, zeroline=False, side="right"),
        yaxis2=dict(gridcolor=_GRID, zeroline=False, side="right"),
    ))
    return fig


def _build_hsgt_chart(content: str) -> go.Figure | None:
    df = _parse_md_table(content)
    if df is None or len(df.columns) < 2:
        return None

    date_col, net_col = df.columns[0], df.columns[1]
    df[net_col] = _to_numeric(df[net_col])
    df = df.dropna(subset=[net_col])

    colors = [_GREEN if v >= 0 else _RED for v in df[net_col]]
    fig = go.Figure(go.Bar(
        x=df[date_col], y=df[net_col], name="净流入",
        marker_color=colors, marker_line_width=0,
        marker_line_color="rgba(0,0,0,0)",
    ))
    title = "北向资金" if "北向" in content else "南向资金"
    fig.update_layout(**_terminal_layout(
        title=dict(text=title, font=dict(size=13)),
        height=350, yaxis_title="净流入 (亿)",
    ))
    return fig


def _build_indicator_chart(content: str) -> go.Figure | None:
    ma_match = re.search(r"### MA.*?\n(.*?)(?=\n###|\Z)", content, re.DOTALL)
    if not ma_match:
        return None

    df = _parse_md_table(ma_match.group(1))
    if df is None:
        return None

    values = [_to_numeric(df[c]).iloc[0] for c in df.columns]
    ma_colors = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6"]
    fig = go.Figure(go.Bar(
        x=list(df.columns), y=values, name="MA",
        marker_color=ma_colors[: len(df.columns)],
        text=[f"{v:.2f}" for v in values],
        textposition="outside", textfont=dict(color=_FONT, size=10),
    ))

    state_match = re.search(r"### Technical State Analysis\n(.*?)$", content, re.DOTALL)
    if state_match:
        state_text = state_match.group(1).strip()
        fig.add_annotation(
            text=state_text.replace("- **", "").replace("**:", ":").replace("\n", "<br>"),
            xref="paper", yref="paper", x=0.5, y=-0.18,
            showarrow=False, font=dict(size=10, color="#64748b"),
        )

    fig.update_layout(**_terminal_layout(
        title=dict(text="技术指标", font=dict(size=13)),
        height=380, yaxis_title="价格",
        margin=dict(l=40, r=16, t=36, b=80),
    ))
    return fig


def _build_quote_chart(content: str) -> go.Figure | None:
    stocks = re.findall(
        r"\*\*(\d+)\*\*\s+(.+?)\n\s+现价:\s*([\d.]+).*?涨跌:\s*([-\d.]+)",
        content,
    )
    if not stocks:
        return None

    codes, names, prices, changes = zip(*stocks)
    changes = [float(c) for c in changes]
    colors = [_GREEN if c >= 0 else _RED for c in changes]

    fig = go.Figure(go.Bar(
        x=[f"{c} {n}" for c, n in zip(codes, names)],
        y=changes, name="涨跌幅",
        marker_color=colors,
        text=[f"{c:+.2f}%" for c in changes],
        textposition="outside", textfont=dict(color=_FONT, size=11),
    ))
    fig.update_layout(**_terminal_layout(
        title=dict(text="实时行情", font=dict(size=13)),
        height=320, yaxis_title="涨跌幅 (%)",
    ))
    return fig


def _build_block_chart(content: str) -> go.Figure | None:
    df = _parse_md_table(content)
    if df is None or len(df.columns) < 2:
        return None

    name_col, change_col = df.columns[0], df.columns[1]
    df[change_col] = _to_numeric(df[change_col])
    df = df.dropna(subset=[change_col]).head(15)

    colors = [_GREEN if v >= 0 else _RED for v in df[change_col]]
    fig = go.Figure(go.Bar(
        x=df[name_col], y=df[change_col], name="涨跌幅",
        marker_color=colors,
    ))
    fig.update_layout(**_terminal_layout(
        title=dict(text="板块行情", font=dict(size=13)),
        height=350, yaxis_title="涨跌幅 (%)",
    ))
    return fig


def _build_financial_chart(content: str) -> go.Figure | None:
    df = _parse_md_table(content)
    if df is None or len(df.columns) < 2:
        return None

    label_col = df.columns[0]
    bar_colors = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]
    fig = go.Figure()
    for i, col in enumerate(df.columns[1:]):
        vals = _to_numeric(df[col])
        if vals.notna().any():
            fig.add_trace(go.Bar(
                name=col, x=df[label_col], y=vals,
                marker_color=bar_colors[i % len(bar_colors)],
            ))

    fig.update_layout(**_terminal_layout(
        title=dict(text="财务指标", font=dict(size=13)),
        barmode="group", height=350,
    ))
    return fig


def _build_trend_chart(content: str) -> go.Figure | None:
    """Build a line chart for trend data (日期 + 数值列)."""
    df = _parse_md_table(content)
    if df is None or len(df.columns) < 2:
        return None

    date_col = df.columns[0]
    line_colors = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]
    fig = go.Figure()
    for i, col in enumerate(df.columns[1:]):
        vals = _to_numeric(df[col])
        if vals.notna().any():
            fig.add_trace(go.Scatter(
                x=df[date_col], y=vals, name=col,
                mode="lines+markers",
                line=dict(color=line_colors[i % len(line_colors)], width=2),
                marker=dict(size=4),
            ))

    fig.update_layout(**_terminal_layout(
        title=dict(text="趋势图", font=dict(size=13)),
        height=320,
    ))
    return fig


def _build_pie_chart(content: str) -> go.Figure | None:
    """Build a pie chart for distribution data."""
    df = _parse_md_table(content)
    if df is None or len(df.columns) < 2:
        return None

    label_col = df.columns[0]
    value_col = df.columns[1]
    vals = _to_numeric(df[value_col])
    df = df.dropna(subset=[value_col])
    if df.empty:
        return None

    pie_colors = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6",
                  "#06b6d4", "#ec4899", "#7c3aed", "#f97316", "#14b8a6"]
    fig = go.Figure(go.Pie(
        labels=df[label_col],
        values=vals,
        marker=dict(colors=pie_colors, line=dict(color="white", width=2)),
        textinfo="label+percent",
        textfont=dict(size=10),
        hole=0.4,
    ))

    fig.update_layout(**_terminal_layout(
        title=dict(text="占比分布", font=dict(size=13)),
        height=320,
    ))
    return fig


# ── Structured data builders (direct DataFrame → Figure) ─────────────

def _build_kline_structured(data: dict) -> go.Figure | None:
    """Build K-line chart from structured DataFrame."""
    df = data["df"]
    if df is None or df.empty:
        return None

    cols = list(df.columns)
    date_col = data.get("date_col", cols[0])
    open_col = data.get("open_col", cols[1])
    high_col = data.get("high_col", cols[2])
    low_col = data.get("low_col", cols[3])
    close_col = data.get("close_col", cols[4])
    vol_col = data.get("vol_col", cols[5] if len(cols) > 5 else None)

    for c in [open_col, high_col, low_col, close_col]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if vol_col and vol_col in df.columns:
        df[vol_col] = pd.to_numeric(df[vol_col], errors="coerce")

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=[0.72, 0.28],
    )

    fig.add_trace(go.Candlestick(
        x=df[date_col], open=df[open_col], high=df[high_col],
        low=df[low_col], close=df[close_col], name="K线",
        increasing_line_color=_RED, decreasing_line_color=_GREEN,
        increasing_fillcolor=_RED, decreasing_fillcolor=_GREEN,
        increasing_line_width=1.5, decreasing_line_width=1.5,
    ), row=1, col=1)

    if vol_col and vol_col in df.columns:
        colors = [
            "rgba(239,68,68,0.5)" if c >= o else "rgba(34,197,94,0.5)"
            for c, o in zip(df[close_col], df[open_col])
        ]
        fig.add_trace(go.Bar(
            x=df[date_col], y=df[vol_col], name="成交量",
            marker_color=colors, showlegend=False,
        ), row=2, col=1)

    fig.update_layout(**_terminal_layout(
        height=420,
        xaxis_rangeslider_visible=False,
        xaxis2=dict(gridcolor=_GRID, zeroline=False),
        yaxis=dict(gridcolor=_GRID, zeroline=False, side="right"),
        yaxis2=dict(gridcolor=_GRID, zeroline=False, side="right"),
    ))
    return fig


def _build_quote_structured(data: dict) -> go.Figure | None:
    """Build quote chart from structured DataFrame."""
    df = data["df"]
    if df is None or df.empty:
        return None

    name_col = data.get("name_col", df.columns[0])
    change_col = data.get("change_col", df.columns[1])

    df[change_col] = pd.to_numeric(df[change_col], errors="coerce")
    df = df.dropna(subset=[change_col])

    colors = [_GREEN if v >= 0 else _RED for v in df[change_col]]
    fig = go.Figure(go.Bar(
        x=df[name_col], y=df[change_col], name="涨跌幅",
        marker_color=colors,
        text=[f"{v:+.2f}%" for v in df[change_col]],
        textposition="outside", textfont=dict(color=_FONT, size=11),
    ))
    fig.update_layout(**_terminal_layout(
        title=dict(text="实时行情", font=dict(size=13)),
        height=320, yaxis_title="涨跌幅 (%)",
    ))
    return fig


def _build_trend_structured(data: dict) -> go.Figure | None:
    """Build trend line chart from structured DataFrame."""
    df = data["df"]
    if df is None or df.empty:
        return None

    date_col = data.get("date_col", df.columns[0])
    value_cols = data.get("value_cols", list(df.columns[1:]))
    title = data.get("title", "趋势图")
    y_title = data.get("y_title", "")

    line_colors = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]
    fig = go.Figure()
    for i, col in enumerate(value_cols):
        vals = pd.to_numeric(df[col], errors="coerce")
        if vals.notna().any():
            fig.add_trace(go.Scatter(
                x=df[date_col], y=vals, name=col,
                mode="lines+markers",
                line=dict(color=line_colors[i % len(line_colors)], width=2),
                marker=dict(size=4),
            ))

    layout_kwargs = dict(title=dict(text=title, font=dict(size=13)), height=320)
    if y_title:
        layout_kwargs["yaxis_title"] = y_title
    fig.update_layout(**_terminal_layout(**layout_kwargs))
    return fig


# Registry: chart_type → structured builder
_STRUCTURED_BUILDERS = {
    "kline": _build_kline_structured,
    "quote": _build_quote_structured,
    "trend": _build_trend_structured,
}
