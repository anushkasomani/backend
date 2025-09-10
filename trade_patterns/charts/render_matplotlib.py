import io, base64
import matplotlib             
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from typing import Dict, Any, List
from .chart_schema import ChartJSON

#helper function to transform the raw OHLCV data into a Pandas DataFrame.
def _ohlcv_to_df(ohlcv: Dict[str, List[float]]) -> pd.DataFrame:
    # ohlcv = {"t":[...ms],"o":[...],"h":[...],"l":[...],"c":[...],"v":[...]}
    df = pd.DataFrame({
        "time": pd.to_datetime(ohlcv["t"], unit="ms", utc=True),
        "open": ohlcv["o"], "high": ohlcv["h"], "low": ohlcv["l"], "close": ohlcv["c"], "volume": ohlcv["v"]
    }).set_index("time")
    return df

# def render_png(ohlcv: Dict[str, Any], overlays: ChartJSON, width=900, height=500) -> str:
#     df = _ohlcv_to_df(ohlcv)
#     fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)

#     # Simple OHLC (candles)
#     x = mdates.date2num(df.index.to_pydatetime())
#     for i, (t, row) in enumerate(df.iterrows()):
#         color = "green" if row["close"] >= row["open"] else "red"
#         ax.plot([x[i], x[i]], [row["low"], row["high"]], linewidth=1, color=color)
#         ax.add_line(plt.Line2D([x[i]-0.2, x[i]+0.2], [row["open"], row["open"]], color=color, linewidth=3))
#         ax.add_line(plt.Line2D([x[i]-0.2, x[i]+0.2], [row["close"], row["close"]], color=color, linewidth=3))

#     # Overlays
#     for shp in overlays.get("series", []):
#         t = shp.get("type")
#         if t == "line":
#             pts = shp["points"]
#             ax.plot([mdates.epoch2num(p[0]/1000) for p in pts], [p[1] for p in pts],
#                     linestyle="--" if shp.get("style", {}).get("dashed") else "-",
#                     linewidth=shp.get("style", {}).get("width", 1))
#         elif t == "ray":
#             start = shp["from_"]
#             x0 = mdates.epoch2num(start[0]/1000)
#             y0 = start[1]
#             x1 = x[-1]
#             ax.plot([x0, x1], [y0, y0], linestyle="--")
#         elif t == "box":
#             p1, p2 = shp["p1"], shp["p2"]
#             xs = [mdates.epoch2num(p1[0]/1000), mdates.epoch2num(p2[0]/1000)]
#             ys = [p1[1], p2[1]]
#             ax.fill_between(xs, ys[0], ys[1], alpha=shp.get("style", {}).get("alpha", 0.1))
#         elif t == "poly":
#             pts = shp["points"]
#             ax.plot([mdates.epoch2num(p[0]/1000) for p in pts], [p[1] for p in pts])
#         elif t == "label":
#             at = shp["at"]; ax.text(mdates.epoch2num(at[0]/1000), at[1], shp["text"])
#         elif t == "level":
#             y = shp["y"]; ax.axhline(y, linestyle="--")

#     ax.xaxis_date(); ax.set_title("Auto Chart")
#     fig.autofmt_xdate()
#     buf = io.BytesIO()
#     plt.tight_layout()
#     plt.savefig(buf, format="png", bbox_inches="tight")
#     plt.close(fig)
#     return base64.b64encode(buf.getvalue()).decode("ascii")


def render_png(ohlcv: Dict[str, Any], overlays: ChartJSON, width=900, height=500) -> str:
    df = _ohlcv_to_df(ohlcv)

    def _ms_to_num(ms: int):
        # ms -> python datetime -> matplotlib float
        return mdates.date2num(pd.to_datetime(int(ms), unit='ms').to_pydatetime())

    # create a 2-row figure: main price chart and a smaller volume chart below
    fig = plt.figure(figsize=(width/100, height/100), dpi=100)
    gs = fig.add_gridspec(2, 1, height_ratios=(3, 1), hspace=0.05)
    ax = fig.add_subplot(gs[0, 0])
    ax_vol = fig.add_subplot(gs[1, 0], sharex=ax)

    # Simple OHLC (candles)
    x = mdates.date2num(df.index.to_pydatetime())
    # estimate candle width (in days) and scale up the body width for visibility
    if len(x) > 1:
        base_step = (x[1] - x[0])
        candle_width = base_step * 0.9
        body_width = base_step * 0.6
    else:
        candle_width = 0.6
        body_width = 0.4

    for i, (t, row) in enumerate(df.iterrows()):
        color = "green" if row["close"] >= row["open"] else "red"
    ax.plot([x[i], x[i]], [row["low"], row["high"]], linewidth=1, color=color)
    # draw thicker bodies so candles are visible at small sizes
    ax.add_line(plt.Line2D([x[i]-body_width/2, x[i]+body_width/2], [row["open"], row["open"]], color=color, linewidth=4))
    ax.add_line(plt.Line2D([x[i]-body_width/2, x[i]+body_width/2], [row["close"], row["close"]], color=color, linewidth=4))

    # Volume as bars in the lower axis
    try:
        vol_colors = ["green" if c >= o else "red" for c, o in zip(df["close"].values, df["open"].values)]
        # make volume bars slightly wider to be visible
        ax_vol.bar(x, df["volume"].values, width=max(candle_width * 0.9, 0.02), color=vol_colors, alpha=0.7)
        ax_vol.set_ylabel("Vol")
    except Exception:
        pass

    # Overlays: support style keys: color, dashed, width, alpha
    for shp in overlays.get("series", []):
        t = shp.get("type")
        style = shp.get("style", {}) or {}
        color = style.get("color")
        if t == "line":
            pts = shp.get("points", [])
            xs = [_ms_to_num(p[0]) for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(xs, ys,
                    linestyle="--" if style.get("dashed") else "-",
                    linewidth=style.get("width", 1),
                    color=color)
        elif t == "ray":
            start = shp.get("from_")
            x0 = _ms_to_num(start[0])
            y0 = start[1]
            x1 = x[-1]
            ax.plot([x0, x1], [y0, y0], linestyle="--", color=color)
        elif t == "box":
            p1, p2 = shp.get("p1"), shp.get("p2")
            xs = [_ms_to_num(p1[0]), _ms_to_num(p2[0])]
            ys = [p1[1], p2[1]]
            ax.fill_between(xs, ys[0], ys[1], alpha=style.get("alpha", 0.1), color=color)
        elif t == "poly":
            pts = shp.get("points", [])
            xs = [_ms_to_num(p[0]) for p in pts]
            ys = [p[1] for p in pts]
            if style.get("alpha") is not None:
                ax.fill(xs, ys, alpha=style.get("alpha", 0.2), color=color)
            else:
                ax.plot(xs, ys, color=color)
        elif t == "label":
            at = shp.get("at")
            ax.text(_ms_to_num(at[0]), at[1], shp.get("text", ""), color=color)
        elif t == "level":
            y = shp.get("y")
            ax.axhline(y, linestyle="--", color=color or "grey", linewidth=style.get("width", 1), alpha=style.get("alpha", 0.8))
        elif t == "volume":
            # optional custom volume overlay: points = [(t_ms, vol), ...]
            pts = shp.get("points", [])
            if pts:
                xs = [_ms_to_num(p[0]) for p in pts]
                ys = [p[1] for p in pts]
                ax_vol.bar(xs, ys, width=candle_width, color=color or "gray", alpha=style.get("alpha", 0.6))

    ax.xaxis_date(); ax.set_title("Auto Chart")
    fig.autofmt_xdate()
    buf = io.BytesIO()
    plt.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()