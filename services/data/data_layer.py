import time, ccxt, pandas as pd, requests
from datetime import datetime, timedelta, timezone

SYMBOL_TO_CG = {"BTC":"bitcoin","ETH":"ethereum","SOL":"solana"}

def ccxt_ohlcv(exchange_id="binanceus", pair="BTC/USDT", timeframe="1d", since_days=540):
    ex = getattr(ccxt, exchange_id)()
    since = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp() * 1000)
    rows = []
    while True:
        batch = ex.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=1000)
        if not batch: break
        rows += batch
        since = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(ex.rateLimit/1000)
    df = pd.DataFrame(rows, columns=["t","open","high","low","close","volume"]).set_index("t")
    df.index = pd.to_datetime(df.index, unit="ms", utc=True)
    return df

def coingecko_ohlcv(gecko_id: str, days=540):
    base = "https://api.coingecko.com/api/v3"
    end = int(time.time()); start = end - days*24*3600
    url = f"{base}/coins/{gecko_id}/market_chart/range"
    r = requests.get(url, params={"vs_currency":"usd","from":start,"to":end}, timeout=20)
    r.raise_for_status()
    j = r.json()
    dfp = pd.DataFrame(j["prices"], columns=["t","close"]).set_index("t")
    dfv = pd.DataFrame(j["total_volumes"], columns=["t","volume"]).set_index("t")
    df = pd.concat([dfp, dfv], axis=1)
    df.index = pd.to_datetime(df.index, unit="ms", utc=True)
    # fabricate hi/lo when missing (fallback only)
    df["open"] = df["close"]; df["high"] = df["close"]; df["low"] = df["close"]
    return df[["open","high","low","close","volume"]]

def load_universe(universe: list[str], since_days=540) -> dict[str, pd.DataFrame]:
    out = {}
    for sym in universe:
        pair = f"{sym}/USDT"
        try:
            df = ccxt_ohlcv("binanceus", pair, "1d", since_days)
            print(f"✅ Got {sym} from CCXT")
            out[sym] = df
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"❌ CCXT failed for {sym}: {e}")
            out[sym] = coingecko_ohlcv(SYMBOL_TO_CG[sym], since_days)
    return out


def top_tickers_from_coingecko(vs_currency: str = "usd", per_page: int = 50) -> list[str]:
    """Return top tickers (symbols) by 24h volume using CoinGecko /coins/markets.
    Returns uppercase ticker symbols (eg. 'BTC', 'ETH').
    """
    base = "https://api.coingecko.com/api/v3"
    url = f"{base}/coins/markets"
    try:
        r = requests.get(url, params={"vs_currency": vs_currency, "order": "volume_desc", "per_page": per_page, "page": 1}, timeout=10)
        r.raise_for_status()
        j = r.json()
        syms = []
        for item in j:
            s = item.get("symbol")
            if s:
                syms.append(s.upper())
        return syms
    except Exception:
        return ["BTC","ETH","SOL","BNB","DOGE","SHIB","PEPE"]


