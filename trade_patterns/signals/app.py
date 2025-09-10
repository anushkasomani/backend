from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any
from .scanner import scan, DETECTORS
import base64
from ..charts.render_matplotlib import render_png
from ..data.ohlcv_intraday import load_ohlcv
from .render_helpers import render_cards_to_base64
import io
from fastapi.responses import StreamingResponse

app = FastAPI()

class ScanReq(BaseModel):
    symbols: List[str] = Field(default_factory=lambda: ["BTC","ETH","SOL","DOGE","PEPE"])
    tf: Literal["1m","3m","5m","15m","30m","1h","2h","4h","6h","12h","1d"] = "5m"
    patterns: List[str] = Field(default_factory=lambda: list(DETECTORS.keys()))
    filters: Optional[Dict[str, Any]] = None
    sort: Literal["prob"] = "prob"
    limit: int = 12
    bars: int = 720
    sensitivity: float = 1.0


class RenderScanReq(ScanReq):
    render: bool = True
    max_render: int = 6
    png_width: int = 900
    png_height: int = 500
    png_binary: bool = False

@app.get("/signals/describe")
def describe():
    return {"patterns": list(DETECTORS.keys()), "tfs": ["1m","3m","5m","15m","30m","1h","2h","4h","6h","12h","1d"]}

@app.post("/signals/scan")
def do_scan(req: ScanReq):
    try:
        filt = req.filters or {}
        cards = scan(
            symbols=req.symbols, tf=req.tf, patterns=req.patterns,
            indicator_filters=filt.get("indicators"),
            recent_breakout_flag=bool(filt.get("recent_breakout", False)),
            recency_bars=int(filt.get("recency_bars", 5)),
            bars=req.bars, sort=req.sort, limit=req.limit, sensitivity=req.sensitivity
        )
        return cards
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/signals/scan_and_render")
def do_scan_and_render(req: RenderScanReq):
    try:
        # reuse existing scanner
        filt = req.filters or {}
        cards = scan(
            symbols=req.symbols, tf=req.tf, patterns=req.patterns,
            indicator_filters=filt.get("indicators"),
            recent_breakout_flag=bool(filt.get("recent_breakout", False)),
            recency_bars=int(filt.get("recency_bars", 5)),
            bars=req.bars, sort=req.sort, limit=req.limit,
            sensitivity=(0.6 if req.sensitivity == 1.0 and req.tf in ["1m","3m","5m"] else req.sensitivity)
        )

        # Optionally render PNGs for top results (bounded)
        rendered_imgs: list[bytes] = []
        if req.render and cards.get("cards"):
            rendered_imgs = render_cards_to_base64(cards.get("cards", []), max_render=req.max_render,
                                                  png_width=req.png_width, png_height=req.png_height,
                                                  default_tf=req.tf, bars=req.bars)

    # Always return JSON cards (png as base64) — ignore png_binary flag so
    # callers always receive consistent JSON objects with embedded images.
    # (Previously we returned a raw StreamingResponse when png_binary=True.)

        return cards
    except Exception as e:
        raise HTTPException(400, str(e))


class ScanDebugReq(ScanReq):
    # keep same fields but purpose is to return diagnostics
    pass


@app.post("/signals/scan_debug")
def do_scan_debug(req: ScanDebugReq):
    try:
        filt = req.filters or {}
        # use same sensitivity auto-lower for short TFs as scan
        sens = (0.6 if req.sensitivity == 1.0 and req.tf in ["1m","3m","5m"] else req.sensitivity)
        # load first symbol only for speed unless user supplies multiple
        sym = req.symbols[0]
        df = load_ohlcv(sym, timeframe=req.tf, bars=req.bars)
        if df is None or df.empty:
            raise HTTPException(404, "no ohlcv for symbol")
        pivots = []
        from .pivots import recent_pivots
        rp = recent_pivots(df, tf=req.tf, sensitivity=sens)
        for p in rp:
            pivots.append({"t": int(p.idx.value // 10**6), "kind": p.kind, "price": float(p.price)})

        dets = []
        for name in req.patterns:
            if name not in DETECTORS:
                dets.append({"pattern": name, "error": "unknown pattern"})
                continue
            try:
                fn = DETECTORS[name]
                det = fn(df, rp, req.tf, sym)
                rec = {"pattern": name, "matched": bool(getattr(det, 'matched', False))}
                if det.card:
                    rec["prob"] = det.card.get("prob")
                    rec["features"] = det.card.get("features")
                dets.append(rec)
            except Exception as e:
                dets.append({"pattern": name, "error": str(e)})

        return {"symbol": sym, "tf": req.tf, "bars": req.bars, "sensitivity": sens, "pivots": pivots, "detectors": dets}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))


class MultiTFReq(BaseModel):
    symbols: List[str] = Field(default_factory=lambda: ["BTC","ETH"])
    tfs: List[Literal["1m","3m","5m","15m","30m","1h","2h","4h","6h","12h","1d"]] = Field(default_factory=lambda: ["5m","1h"])
    patterns: List[str] = Field(default_factory=lambda: list(DETECTORS.keys()))
    bars: int = 720
    sensitivity: float = 1.0
    render: bool = False
    max_render_per_tf: int = 3


@app.post("/signals/scan_multitf")
def do_scan_multitf(req: MultiTFReq):
    try:
        results = {}
        for tf in req.tfs:
            # apply same auto-lower rule for short TFs
            sens = (0.6 if req.sensitivity == 1.0 and tf in ["1m","3m","5m"] else req.sensitivity)
            cards = scan(symbols=req.symbols, tf=tf, patterns=req.patterns, bars=req.bars, sensitivity=sens, limit=50)
            # optionally render small set per-tf
            if req.render and cards.get("cards"):
                for i, card in enumerate(cards.get("cards", [])[: req.max_render_per_tf]):
                    try:
                        sym = card.get("symbol")
                        df = load_ohlcv(sym, timeframe=tf, bars=req.bars)
                        if df is None or df.empty:
                            continue
                        times_ms = (df.index.view("int64") // 1_000_000).tolist()
                        ohlcv = {"t":[int(x) for x in times_ms], "o":[float(x) for x in df["open"].tolist()],
                                 "h":[float(x) for x in df["high"].tolist()], "l":[float(x) for x in df["low"].tolist()],
                                 "c":[float(x) for x in df["close"].tolist()], "v":[float(x) for x in df["volume"].tolist()]}
                        overlays = card.get("overlays") or {"version":1, "series": []}
                        img = render_png(ohlcv, overlays, width=800, height=400)
                        card["png_base64"] = base64.b64encode(img).decode("ascii")
                    except Exception as e:
                        card["png_error"] = str(e)
            results[tf] = cards
        return results
    except Exception as e:
        raise HTTPException(400, str(e))


