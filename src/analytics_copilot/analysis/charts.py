"""Automatic visualisation: choose a chart form from the shape of the result.

    single value            -> no chart (a stat tile; a plot of one number says nothing)
    time + measure          -> line (one line per dimension value when there is one, max 8)
    dimension + measure     -> bar, sorted by value, horizontal when labels are long or many
    two dimensions + measure-> grouped bar (second dimension as colour, max 8)
    two measures            -> scatter
    anything else           -> table only

The output is a Vega-Lite spec: plain JSON the web UI renders with vega-embed and
`render_png` turns into an image for reports. Colours are the categorical palette from
the dataviz reference (validated for colour-vision deficiency, assigned in fixed order).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from analytics_copilot.analysis.profile import Profile, as_time

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
TEXT, TEXT_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
MAX_SERIES = 8
MAX_BARS = 25

CONFIG = {
    "background": SURFACE,
    "font": "IBM Plex Sans, Segoe UI, sans-serif",
    "view": {"stroke": None},
    "axisQuantitative": {"format": "~s", "tickCount": 6},
    "axis": {"labelColor": TEXT_2, "titleColor": TEXT_2, "gridColor": GRID, "domainColor": GRID,
             "tickColor": GRID, "labelFontSize": 11, "titleFontSize": 11,
             "titleFontWeight": "normal"},
    "legend": {"labelColor": TEXT_2, "titleColor": TEXT_2, "orient": "top"},
    "title": {"color": TEXT, "fontSize": 13, "anchor": "start", "fontWeight": 600},
    "range": {"category": SERIES},
    "line": {"strokeWidth": 2},
    "point": {"size": 64, "filled": True},
    "bar": {"cornerRadiusEnd": 4},
}


@dataclass
class Chart:
    kind: str           # line | bar | grouped_bar | scatter | none
    reason: str
    spec: dict | None   # Vega-Lite


def choose_chart(df: pd.DataFrame, prof: Profile, title: str = "") -> Chart:
    if df.empty:
        return Chart("none", "no rows", None)
    shape = prof.shape
    if shape == "single_value" and len(df) == 1:
        return Chart("none", "a single number reads best as a stat, not a chart", None)

    data = df.copy()
    if shape == "time_series":
        m = prof.measures[0]
        data[prof.time] = as_time(data[prof.time])
        temporal = pd.api.types.is_datetime64_any_dtype(data[prof.time])
        if temporal:
            data[prof.time] = data[prof.time].dt.strftime("%Y-%m-%d")
        x = {"field": prof.time, "type": "temporal" if temporal else "ordinal",
             "title": None}
        enc = {"x": x, "y": {"field": m, "type": "quantitative", "title": _title(m)},
               "tooltip": [{"field": c, "type": _vl_type(df, c, prof)} for c in df.columns]}
        reason = "time on the x-axis, so a line shows the change over time"
        if prof.dimensions:
            dim = prof.dimensions[0]
            top = data.groupby(dim)[m].sum().sort_values(ascending=False).index[:MAX_SERIES]
            data = data[data[dim].isin(top)]
            enc["color"] = {"field": dim, "type": "nominal", "title": None,
                            "sort": list(map(str, top))}
            reason += f", one line per {dim}"
        mark = {"type": "line", "point": len(data) <= 40}
        return Chart("line", reason, _spec(data, mark, enc, title))

    if shape == "breakdown":
        dim, m = prof.dimensions[0], prof.measures[0]
        if len(prof.dimensions) >= 2:
            dim2 = prof.dimensions[1]
            if data[dim2].nunique() <= MAX_SERIES and data[dim].nunique() <= MAX_BARS:
                enc = {"x": {"field": dim, "type": "nominal", "title": None},
                       "xOffset": {"field": dim2},
                       "y": {"field": m, "type": "quantitative", "title": _title(m)},
                       "color": {"field": dim2, "type": "nominal", "title": None},
                       "tooltip": [{"field": c, "type": _vl_type(df, c, prof)}
                                   for c in df.columns]}
                return Chart("grouped_bar", f"two categories: {dim2} as colour within {dim}",
                             _spec(data, {"type": "bar"}, enc, title))
        data = data.groupby(dim, as_index=False, sort=False)[m].sum()
        data = data.sort_values(m, ascending=False).head(MAX_BARS)
        long_labels = data[dim].astype(str).str.len().max() > 12 or len(data) > 6
        cat = {"field": dim, "type": "nominal", "sort": "-x" if long_labels else "-y",
               "title": None}
        val = {"field": m, "type": "quantitative", "title": _title(m)}
        enc = {"y": cat, "x": val} if long_labels else {"x": cat, "y": val}
        enc["tooltip"] = [{"field": dim, "type": "nominal"}, {"field": m, "type": "quantitative",
                                                               "format": ",.2f"}]
        enc["color"] = {"value": SERIES[0]}
        orient = "horizontal" if long_labels else "vertical"
        return Chart("bar", f"compare {m} across {dim} ({orient}, sorted)",
                     _spec(data, {"type": "bar"}, enc, title))

    if shape == "relationship":
        a, b = prof.measures[:2]
        enc = {"x": {"field": a, "type": "quantitative", "title": _title(a)},
               "y": {"field": b, "type": "quantitative", "title": _title(b)},
               "color": {"value": SERIES[0]},
               "tooltip": [{"field": c, "type": _vl_type(df, c, prof)} for c in df.columns]}
        return Chart("scatter", f"two measures: {a} against {b}",
                     _spec(data, {"type": "point"}, enc, title))
    return Chart("none", "no measure to plot; shown as a table", None)


def contribution_chart(df: pd.DataFrame, dim: str, value: str, title: str) -> Chart:
    """Diverging bars for change contributions: positive blue, negative red, zero line."""
    data = df[[dim, value]].copy()
    data["direction"] = data[value].map(lambda v: "increase" if v >= 0 else "decrease")
    enc = {"y": {"field": dim, "type": "nominal", "sort": {"field": value, "order": "ascending"},
                 "title": None},
           "x": {"field": value, "type": "quantitative", "title": _title(value)},
           "color": {"field": "direction", "type": "nominal", "title": None,
                     "scale": {"domain": ["increase", "decrease"],
                               "range": [SERIES[0], SERIES[7]]}},
           "tooltip": [{"field": dim, "type": "nominal"},
                       {"field": value, "type": "quantitative", "format": ",.0f"}]}
    spec = _spec(data, {"type": "bar"}, enc, title)
    spec["layer"] = [{"mark": spec.pop("mark"), "encoding": spec.pop("encoding")},
                     {"mark": {"type": "rule", "color": TEXT_2}, "encoding": {"x": {"datum": 0}}}]
    return Chart("bar", "change contributions per segment, diverging from zero", spec)


def _spec(data: pd.DataFrame, mark: dict, enc: dict, title: str) -> dict:
    records = data.astype(object).where(pd.notna(data), None).to_dict("records")
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "title": title or None, "width": "container", "height": 280,
            "data": {"values": _jsonable(records)}, "mark": mark, "encoding": enc,
            "config": CONFIG}


def _jsonable(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        row = {}
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                v = v.isoformat()
            elif hasattr(v, "item"):
                v = v.item()
            row[str(k)] = v
        out.append(row)
    return out


def _title(col: str) -> str:
    return col.replace("_", " ")


def _vl_type(df: pd.DataFrame, col: str, prof: Profile) -> str:
    if col == prof.time:
        return "temporal" if not pd.api.types.is_numeric_dtype(df[col]) else "ordinal"
    return "quantitative" if col in prof.measures else "nominal"


def render_png(spec: dict, scale: float = 2.0) -> bytes:
    import vl_convert as vlc

    spec = dict(spec, width=640)
    return vlc.vegalite_to_png(spec, scale=scale)
