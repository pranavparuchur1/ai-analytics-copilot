"""Work out what each result column is: a time axis, a measure, or a dimension."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

TIME_NAME = re.compile(r"(^|_)(date|day|week|month|quarter|year|period|time|at|on)($|_)", re.I)
ID_NAME = re.compile(r"(^id$|_id$)", re.I)


@dataclass
class Profile:
    time: str | None = None
    measures: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)

    @property
    def shape(self) -> str:
        """Short label the chart and analysis steps branch on."""
        if self.time and self.measures:
            return "time_series"
        if self.dimensions and self.measures:
            return "breakdown"
        if len(self.measures) >= 2:
            return "relationship"
        if len(self.measures) == 1 and not self.dimensions:
            return "single_value"
        return "table"


def profile(df: pd.DataFrame) -> Profile:
    p = Profile()
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s) or _is_date_objects(s):
            p.time = p.time or col
        elif pd.api.types.is_bool_dtype(s):
            p.dimensions.append(col)
        elif pd.api.types.is_numeric_dtype(s):
            if ID_NAME.search(col):
                p.ids.append(col)
            elif TIME_NAME.search(col) and _looks_like_period_number(s) and p.time is None:
                p.time = col  # e.g. year = 2024, 2025 or month = 1..12
            else:
                p.measures.append(col)
        else:
            p.dimensions.append(col)
    return p


def _is_date_objects(s: pd.Series) -> bool:
    import datetime as dt

    vals = s.dropna()
    return len(vals) > 0 and all(isinstance(v, dt.date) for v in vals.head(20))


def _looks_like_period_number(s: pd.Series) -> bool:
    vals = s.dropna()
    if vals.empty or not (vals == vals.round()).all():
        return False
    return bool(vals.between(1, 12).all() or vals.between(1990, 2100).all())


def as_time(s: pd.Series) -> pd.Series:
    """Time column as pandas datetimes (tz dropped), or numbers for year/month columns."""
    if pd.api.types.is_numeric_dtype(s):
        return s
    t = pd.to_datetime(s, errors="coerce", utc=True)
    return t.dt.tz_localize(None)
