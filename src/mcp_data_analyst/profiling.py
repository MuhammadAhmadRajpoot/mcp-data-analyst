"""Column statistics, distributions, correlations and data-quality checks for a DataFrame."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .models import (
    ColumnProfile,
    Correlation,
    DatasetProfile,
    HistogramBin,
    NumericSummary,
    QualityIssue,
    ValueCount,
)

MISSING_WARN_PCT = 5.0
OUTLIER_WARN_PCT = 1.0
HISTOGRAM_BINS = 10
TOP_VALUES = 5


def _f(value: object) -> float | None:
    """Round a numeric statistic, mapping NaN/inf to None."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(number) else round(number, 4)


def _numeric_summary(series: pd.Series) -> NumericSummary:
    values = series.dropna().astype("float64")
    if values.empty:
        return NumericSummary(
            mean=None,
            std=None,
            min=None,
            p25=None,
            median=None,
            p75=None,
            max=None,
            skew=None,
            outlier_count=0,
            histogram=[],
        )
    q1, q2, q3 = values.quantile([0.25, 0.5, 0.75])
    iqr = q3 - q1
    outliers = int(((values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr)).sum()) if iqr > 0 else 0
    counts, edges = np.histogram(values, bins=HISTOGRAM_BINS)
    return NumericSummary(
        mean=_f(values.mean()),
        std=_f(values.std()),
        min=_f(values.min()),
        p25=_f(q1),
        median=_f(q2),
        p75=_f(q3),
        max=_f(values.max()),
        skew=_f(values.skew()),
        outlier_count=outliers,
        histogram=[
            HistogramBin(lower=float(edges[i]), upper=float(edges[i + 1]), count=int(c)) for i, c in enumerate(counts)
        ],
    )


def profile_column(series: pd.Series, duck_type: str) -> ColumnProfile:
    nulls = int(series.isna().sum())
    total = len(series)
    profile = ColumnProfile(
        name=str(series.name),
        type=duck_type,
        null_count=nulls,
        null_pct=round(100 * nulls / total, 2) if total else 0.0,
        distinct_count=int(series.nunique(dropna=True)),
    )
    if pd.api.types.is_bool_dtype(series):
        profile.top_values = _top_values(series)
    elif pd.api.types.is_numeric_dtype(series):
        profile.numeric = _numeric_summary(series)
    elif pd.api.types.is_datetime64_any_dtype(series) or duck_type.upper().startswith(("DATE", "TIMESTAMP")):
        non_null = pd.to_datetime(series.dropna())
        if not non_null.empty:
            profile.min_value = non_null.min().isoformat()
            profile.max_value = non_null.max().isoformat()
    else:
        profile.top_values = _top_values(series)
    return profile


def _top_values(series: pd.Series) -> list[ValueCount]:
    counts = series.astype("string").value_counts(dropna=True).head(TOP_VALUES)
    return [ValueCount(value=str(v), count=int(c)) for v, c in counts.items()]


def top_correlations(df: pd.DataFrame, limit: int = 10) -> list[Correlation]:
    numeric = df.select_dtypes(include="number").loc[:, lambda d: d.nunique() > 1]
    if numeric.shape[1] < 2:
        return []
    corr = numeric.astype("float64").corr(method="pearson")
    pairs = [
        Correlation(column_a=a, column_b=b, pearson=round(float(corr.loc[a, b]), 4))
        for i, a in enumerate(corr.columns)
        for b in corr.columns[i + 1 :]
        if np.isfinite(corr.loc[a, b])
    ]
    return sorted(pairs, key=lambda c: abs(c.pearson), reverse=True)[:limit]


def quality_issues(df: pd.DataFrame, columns: list[ColumnProfile], duplicate_rows: int) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    total = len(df)
    if duplicate_rows:
        issues.append(
            QualityIssue(
                severity="warning",
                kind="duplicates",
                column=None,
                detail=f"{duplicate_rows} fully duplicated rows ({100 * duplicate_rows / total:.2f}% of rows).",
            )
        )
    for col in columns:
        if col.null_count:
            severity = "warning" if col.null_pct >= MISSING_WARN_PCT else "info"
            issues.append(
                QualityIssue(
                    severity=severity,
                    kind="missing",
                    column=col.name,
                    detail=f"{col.null_count} missing values ({col.null_pct}%).",
                )
            )
        if total > 1 and col.distinct_count <= 1:
            issues.append(
                QualityIssue(
                    severity="info", kind="constant", column=col.name, detail="Column has a single distinct value."
                )
            )
        if col.numeric and col.numeric.outlier_count:
            pct = 100 * col.numeric.outlier_count / max(total - col.null_count, 1)
            issues.append(
                QualityIssue(
                    severity="warning" if pct >= OUTLIER_WARN_PCT else "info",
                    kind="outliers",
                    column=col.name,
                    detail=f"{col.numeric.outlier_count} values outside 1.5*IQR ({pct:.2f}%); "
                    f"range {col.numeric.min}..{col.numeric.max}, median {col.numeric.median}.",
                )
            )
        if col.top_values is not None and total >= 50 and col.distinct_count == total - col.null_count:
            issues.append(
                QualityIssue(
                    severity="info",
                    kind="high_cardinality",
                    column=col.name,
                    detail="Every non-null value is unique (likely an identifier or free text).",
                )
            )
    return issues


def profile_dataframe(name: str, row_count: int, df: pd.DataFrame, types: dict[str, str]) -> DatasetProfile:
    """Build the full profile of ``df`` (which may be a sample of a ``row_count``-row table)."""
    columns = [profile_column(df[c], types.get(str(c), str(df[c].dtype))) for c in df.columns]
    duplicates = int(df.astype("string").duplicated().sum()) if len(df) else 0
    return DatasetProfile(
        name=name,
        row_count=row_count,
        profiled_rows=len(df),
        duplicate_rows=duplicates,
        columns=columns,
        top_correlations=top_correlations(df),
        issues=quality_issues(df, columns, duplicates),
    )
