from __future__ import annotations
import numpy as np, pandas as pd
from dataclasses import dataclass
from typing import List, Literal

PivotType = Literal["HIGH","LOW"]

@dataclass
class Pivot:
    idx: pd.Timestamp
    kind: PivotType
    price: float

def zigzag_by_pct(close: pd.Series, pct: float = 0.015) -> List[Pivot]:
    pivots: List[Pivot] = []
    if close.empty: return pivots
    trend = 0
    last_idx = close.index[0]
    last_price = float(close.iloc[0])
    for i, p in close.items():
        chg = (p / last_price) - 1.0
        if trend >= 0 and chg <= -pct:
            pivots.append(Pivot(last_idx, "HIGH", last_price))
            trend = -1; last_idx = i; last_price = float(p)
        elif trend <= 0 and chg >= pct:
            pivots.append(Pivot(last_idx, "LOW", last_price))
            trend = 1; last_idx = i; last_price = float(p)
        else:
            if (trend >= 0 and p > last_price) or (trend <= 0 and p < last_price) or trend == 0:
                last_idx = i; last_price = float(p)
    return pivots

def recent_pivots(df: pd.DataFrame, tf: str, sensitivity: float = 1.0) -> List[Pivot]:
    """Return recent pivots; sensitivity <1.0 => more pivots (lower threshold)."""
    tf_eps = {
        "1m":0.004, "3m":0.005, "5m":0.006, "15m":0.008, "30m":0.010,
        "1h":0.012, "2h":0.015, "4h":0.018, "6h":0.020, "12h":0.025, "1d":0.030
    }
    base_pct = tf_eps.get(tf, 0.015)
    pct = max(0.0005, base_pct * max(0.5, sensitivity))
    return zigzag_by_pct(df["close"], pct=pct)
