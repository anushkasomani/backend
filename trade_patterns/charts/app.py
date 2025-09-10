from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import Dict, Any, Optional
from .render_matplotlib import render_png
from services.data.ohlcv import ccxt_ohlcv, cg_market_chart_range
import pandas as pd

app = FastAPI()


class ChartReq(BaseModel):
    ohlcv: Optional[Dict[str, Any]] = None
    overlays: Optional[Dict[str, Any]] = None
    width: int = 900
    height: int = 500


class SymbolChartReq(BaseModel):
    symbol: str
    days: int = 30
    overlays: Optional[Dict[str, Any]] = None
    width: int = 900
    height: int = 500
    source: Optional[str] = None  # 'ccxt' or 'cg' to force provider


@app.post("/charts/render")
def render(req: ChartReq):
    try:
        if req.ohlcv is None:
            raise HTTPException(status_code=400, detail="ohlcv is required for this endpoint or use /charts/render_by_symbol")
        image_bytes = render_png(req.ohlcv, req.overlays or {"series": []}, req.width, req.height)
        return Response(content=image_bytes, media_type="image/png")
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/charts/render_by_symbol")
def render_by_symbol(req: SymbolChartReq):
    """
    Fetch OHLCV for a symbol (e.g., BTC) and render chart.
    If overlays not provided, a simple default overlay (level at last close) is generated.
    """
    try:
        pair = f"{req.symbol.upper()}/USDT"
        df = None
        ccxt_exc = None

        # If user forced a source, respect it
        forced = (req.source or "").lower() if req.source else None

        # try ccxt-based fetch first unless forced to CG
        if forced != "cg":
            try:
                df = ccxt_ohlcv(pair, since_days=req.days)
            except Exception as e:
                ccxt_exc = e

        # fallback to coin-gecko if ccxt failed or returned empty, or if forced
        if df is None or (hasattr(df, 'empty') and df.empty):
            if forced == "ccxt":
                # user forced ccxt but it failed
                raise HTTPException(status_code=500, detail=f"ccxt failed: {ccxt_exc}")
            cg_exc = None
            try:
                df = cg_market_chart_range(req.symbol, days=req.days)
            except Exception as e:
                cg_exc = e
                # If ccxt also failed, show both errors to help debugging
                if ccxt_exc is not None:
                    raise HTTPException(status_code=500, detail=f"ccxt error: {ccxt_exc} ; coin-gecko error: {cg_exc}")
                else:
                    raise HTTPException(status_code=500, detail=f"coin-gecko error: {cg_exc}")

        if df is None or (hasattr(df, 'empty') and df.empty):
            raise HTTPException(status_code=500, detail="Failed to fetch OHLCV for symbol")

        # normalize DataFrame to expected columns and index
        if not getattr(df.index, "name", None) == "time":
            df = df.reset_index()
            if "time" not in df.columns and "t" in df.columns:
                df = df.rename(columns={"t": "time"})
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], utc=True)
                df = df.set_index("time")

        # build ohlcv dict with epoch ms
        times_ms = (df.index.view("int64") // 1_000_000).tolist()
        ohlcv = {
            "t": times_ms,
            "o": df["open"].astype(float).tolist(),
            "h": df["high"].astype(float).tolist(),
            "l": df["low"].astype(float).tolist(),
            "c": df["close"].astype(float).tolist(),
            "v": df["volume"].astype(float).tolist(),
        }

        overlays = req.overlays
        if overlays is None:
            # simple default overlay: horizontal level at last close
            last_close = float(df["close"].iloc[-1])
            series = [{"type": "level", "y": last_close, "style": {"alpha": 0.2}}]

            # Add simple moving averages (SMA) as line overlays when enough data exists
            close = df["close"].astype(float)
            # Short & long windows (can tweak)
            sma_short_w = 20
            sma_long_w = 50

            sma_short = close.rolling(window=sma_short_w, min_periods=1).mean()
            sma_long = close.rolling(window=sma_long_w, min_periods=1).mean()

            # Bollinger Bands (short window, 2 sigma)
            bb_std = close.rolling(window=sma_short_w, min_periods=1).std()
            bb_upper = sma_short + 2 * bb_std
            bb_lower = sma_short - 2 * bb_std

            # build points aligned to epoch ms
            times_ms_series = times_ms

            def build_line_points(values):
                pts = []
                for t_ms, v in zip(times_ms_series, values.tolist()):
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        continue
                    pts.append((int(t_ms), float(v)))
                return pts

            short_pts = build_line_points(sma_short)
            long_pts = build_line_points(sma_long)

            # Bollinger polygon: upper points then reversed lower points to form a closed poly
            upper_pts = build_line_points(bb_upper)
            lower_pts = build_line_points(bb_lower)
            bb_poly = None
            if upper_pts and lower_pts and len(upper_pts) == len(lower_pts):
                # create a poly with upper forward and lower reversed
                poly_pts = [(int(u[0]), float(u[1])) for u in upper_pts] + [(int(l[0]), float(l[1])) for l in reversed(lower_pts)]
                bb_poly = poly_pts

            # default styles: short SMA blue, long SMA orange
            if long_pts:
                series.append({"type": "line", "points": long_pts, "style": {"dashed": True, "width": 1, "color": "#ff7f0e"}})
            if short_pts:
                series.append({"type": "line", "points": short_pts, "style": {"dashed": False, "width": 2, "color": "#1f77b4"}})

            if bb_poly:
                series.append({"type": "poly", "points": bb_poly, "style": {"alpha": 0.15, "color": "#1f77b4"}})

            overlays = {"series": series}

        image_bytes = render_png(ohlcv, overlays, req.width, req.height)
        return Response(content=image_bytes, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
