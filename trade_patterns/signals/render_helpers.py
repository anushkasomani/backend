import base64
from typing import List, Dict, Any
from ..data.ohlcv_intraday import load_ohlcv
from ..charts.render_matplotlib import render_png


def render_cards_to_base64(cards: List[Dict[str, Any]], max_render: int = 6,
                           png_width: int = 900, png_height: int = 500,
                           default_tf: str = "5m", bars: int = 720) -> List[bytes]:
    """Render up to max_render cards in-place (adds 'png_base64' or 'png_error')
    and return the list of raw PNG bytes in the same order.
    """
    rendered_imgs: List[bytes] = []
    if not cards:
        return rendered_imgs

    for i, card in enumerate(cards[: max_render]):
        try:
            # Allow callers to provide pre-fetched OHLCV on the card as either
            # a dict with keys t,o,h,l,c,v (lists) or as a pandas DataFrame
            # under `ohlcv` or `ohlcv_df`. Fall back to loading by symbol.
            ohlcv = None
            if isinstance(card.get("ohlcv"), dict):
                ohlcv = card.get("ohlcv")
            elif card.get("ohlcv_df") is not None:
                df = card.get("ohlcv_df")
                # DataFrame -> ohlcv dict
                times_ms = (df.index.view("int64") // 1_000_000).tolist()
                ohlcv = {
                    "t": [int(x) for x in times_ms],
                    "o": [float(x) for x in df["open"].tolist()],
                    "h": [float(x) for x in df["high"].tolist()],
                    "l": [float(x) for x in df["low"].tolist()],
                    "c": [float(x) for x in df["close"].tolist()],
                    "v": [float(x) for x in df["volume"].tolist()],
                }
            else:
                sym = card.get("symbol")
                tf = card.get("tf") or default_tf
                df = load_ohlcv(sym, timeframe=tf, bars=bars)
                if df is None or df.empty:
                    continue
                times_ms = (df.index.view("int64") // 1_000_000).tolist()
                ohlcv = {
                    "t": [int(x) for x in times_ms],
                    "o": [float(x) for x in df["open"].tolist()],
                    "h": [float(x) for x in df["high"].tolist()],
                    "l": [float(x) for x in df["low"].tolist()],
                    "c": [float(x) for x in df["close"].tolist()],
                    "v": [float(x) for x in df["volume"].tolist()],
                }
            overlays = card.get("overlays") or {"version": 1, "series": []}
            img = render_png(ohlcv, overlays, width=png_width, height=png_height)
            rendered_imgs.append(img)
            card["png_base64"] = base64.b64encode(img).decode("ascii")
        except Exception as e:
            card["png_error"] = str(e)

    return rendered_imgs
