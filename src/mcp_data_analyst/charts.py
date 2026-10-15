"""Render bar / line / scatter / histogram charts from a DataFrame with matplotlib.

Uses the object-oriented ``Figure`` API (no ``pyplot`` global state), so rendering is
headless and safe to call from worker threads.
"""

from __future__ import annotations

import io
from typing import Literal

import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from .errors import InvalidArgumentError

ChartType = Literal["bar", "line", "scatter", "histogram"]

MAX_BAR_CATEGORIES = 40

# Single-series styling: one accent hue, recessive grid and axes, text in neutral ink.
SURFACE = "#fcfcfb"
SERIES = "#2a78d6"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e4e3df"


def _require_columns(df: pd.DataFrame, *names: str | None) -> None:
    missing = [n for n in names if n is not None and n not in df.columns]
    if missing:
        raise InvalidArgumentError(f"Column(s) {missing} not in query result. Available: {list(df.columns)}.")


def _require_numeric(df: pd.DataFrame, *names: str) -> None:
    for name in names:
        if not pd.api.types.is_numeric_dtype(df[name]):
            raise InvalidArgumentError(f"Column {name!r} must be numeric for this chart (got {df[name].dtype}).")


def _axis_number(value: float, _pos: int) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:g}"


def _rotate_x_labels(ax: Axes, degrees: int) -> None:
    for label in ax.get_xticklabels():
        label.set_rotation(degrees)
        label.set_horizontalalignment("right")
        label.set_rotation_mode("anchor")


def _style(ax: Axes, title: str, x_label: str, y_label: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=13, color=INK, pad=12, fontweight="bold")
    ax.set_xlabel(x_label, color=INK_MUTED)
    ax.set_ylabel(y_label, color=INK_MUTED)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, length=0)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.yaxis.set_major_formatter(FuncFormatter(_axis_number))


def _bar(ax: Axes, df: pd.DataFrame, x: str, y: str | None) -> None:
    if y is None:
        data = df[x].astype("string").value_counts().head(MAX_BAR_CATEGORIES)
    else:
        _require_numeric(df, y)
        data = df.set_index(df[x].astype("string"))[y].head(MAX_BAR_CATEGORIES)
    ax.bar(list(data.index), list(data.values), color=SERIES, width=0.6)
    if len(data) > 8:
        _rotate_x_labels(ax, 45)


def _line(ax: Axes, df: pd.DataFrame, x: str, y: str) -> None:
    _require_numeric(df, y)
    data = df[[x, y]].dropna().sort_values(x)
    ax.plot(data[x], data[y], color=SERIES, linewidth=2, solid_capstyle="round", solid_joinstyle="round")
    if pd.api.types.is_datetime64_any_dtype(data[x]) or len(data) > 12:
        _rotate_x_labels(ax, 30)


def _scatter(ax: Axes, df: pd.DataFrame, x: str, y: str) -> None:
    _require_numeric(df, x, y)
    ax.scatter(df[x], df[y], s=22, color=SERIES, alpha=0.75, edgecolors=SURFACE, linewidths=0.6)
    ax.grid(axis="x", color=GRID, linewidth=1)


def render_chart(
    df: pd.DataFrame,
    chart_type: ChartType,
    x: str,
    y: str | None = None,
    title: str | None = None,
    bins: int = 20,
) -> bytes:
    """Return the chart as PNG bytes. Raises ``InvalidArgumentError`` on unusable input."""
    if df.empty:
        raise InvalidArgumentError("The query returned no rows; nothing to plot.")
    _require_columns(df, x, y)

    fig = Figure(figsize=(9, 5), dpi=120, facecolor=SURFACE)
    ax = fig.subplots()
    if chart_type == "bar":
        _bar(ax, df, x, y)
    elif chart_type in ("line", "scatter"):
        if y is None:
            raise InvalidArgumentError(f"A {chart_type} chart needs both 'x' and 'y'.")
        (_line if chart_type == "line" else _scatter)(ax, df, x, y)
    elif chart_type == "histogram":
        _require_numeric(df, x)
        ax.hist(df[x].dropna(), bins=max(2, min(bins, 200)), color=SERIES, edgecolor=SURFACE, linewidth=1)
        y = None
    else:
        raise InvalidArgumentError(f"Unknown chart_type {chart_type!r}; use bar, line, scatter or histogram.")

    default_title = f"{y} by {x}" if y else f"Distribution of {x}" if chart_type == "histogram" else f"Count by {x}"
    _style(ax, title or default_title, x, y or "count")
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=SURFACE)
    return buffer.getvalue()
