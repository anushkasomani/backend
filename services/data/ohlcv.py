import time, requests, pandas as pd
from datetime import datetime, timedelta, timezone
import ccxt

COINGECKO_IDS = {"BTC":"bitcoin","ETH":"ethereum","SOL":"solana"}

def ccxt_ohlcv(symbol_pair="BTC/USDT", exchange_id="binance", timeframe="1d", since_days=540):
    ex = getattr(ccxt, exchange_id)()
    since = int((datetime.now(timezone.utc)-timedelta(days=since_days)).timestamp()*1000)
    rows = []
    while True:
        batch = ex.fetch_ohlcv(symbol_pair, timeframe=timeframe, since=since, limit=1000)
        if not batch: break
        rows += batch
        since = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(ex.rateLimit/1000.0)
    df = pd.DataFrame(rows, columns=["t","open","high","low","close","volume"]).set_index("t")
    df.index = pd.to_datetime(df.index, unit="ms", utc=True)
    # Resample to daily OHLCV (open first, high max, low min, close last, volume sum)
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return daily

def cg_market_chart_range(symbol: str, vs="usd", days=540):
    cid = COINGECKO_IDS[symbol]
    end = int(time.time())
    start = end - days*24*3600
    url = f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart/range"
    r = requests.get(url, params={"vs_currency":vs,"from":start,"to":end}, timeout=30)
    r.raise_for_status()
    j = r.json()
    dfp = pd.DataFrame(j["prices"], columns=["t","price"]).set_index("t")
    dfv = pd.DataFrame(j["total_volumes"], columns=["t","volume"]).set_index("t")
    dfp.index = pd.to_datetime(dfp.index, unit="ms", utc=True)
    dfv.index = pd.to_datetime(dfv.index, unit="ms", utc=True)

    # build daily OHLC from the price series
    ohlc = dfp["price"].resample("1D").agg(["first", "max", "min", "last"]).dropna()
    ohlc.columns = ["open", "high", "low", "close"]

    vol = dfv.resample("1D").last()

    df_daily = pd.concat([ohlc, vol], axis=1).dropna()
    return df_daily[["open", "high", "low", "close", "volume"]]

def load_ohlcv(symbol: str, days: int) -> pd.DataFrame:
    try:
        pair = f"{symbol}/USDT"
        return ccxt_ohlcv(pair, since_days=days)
    except Exception:
        return cg_market_chart_range(symbol, days=days)

