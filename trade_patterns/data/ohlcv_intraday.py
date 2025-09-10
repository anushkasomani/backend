import time, ccxt, pandas as pd, requests
from datetime import datetime, timedelta, timezone

#blue chips and meme coins 
COINGECKO = {
    "BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binancecoin","XRP":"ripple",
    "ADA":"cardano","AVAX":"avalanche-2","LTC":"litecoin","LINK":"chainlink","MATIC":"matic-network",
    "DOGE":"dogecoin","SHIB":"shiba-inu","PEPE":"pepe","WIF":"dogwifcoin","BONK":"bonk",
    "FLOKI":"floki","BRETT":"based-brett"
}

#time frames 
VALID_TFS = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","12h","1d"}

def ccxt_ohlcv(symbol="BTC/USDT", exchange_id="binance", timeframe="1h", since_ms=None, limit=1500):
    assert timeframe in VALID_TFS
    ex = getattr(ccxt, exchange_id)()
    rows = []
    if since_ms is None:
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=14)).timestamp() * 1000)
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=1000)
        if not batch: break
        rows += batch
        since_ms = batch[-1][0] + 1
        if len(batch) < 1000 or len(rows) >= limit: break
        time.sleep(ex.rateLimit/1000.0)
    df = pd.DataFrame(rows, columns=["t","open","high","low","close","volume"]).set_index("t")
    df.index = pd.to_datetime(df.index, unit="ms", utc=True)
    return df

#coin gecko function for fallback 
def cg_market_chart_range(symbol: str, vs="usd", start_s: int|None=None, end_s: int|None=None):
    cid = COINGECKO[symbol]
    if end_s is None:
        end_s = int(time.time())
    if start_s is None:
        start_s = end_s - 7*24*3600
    url = f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart/range"
    r = requests.get(url, params={"vs_currency":vs,"from":start_s,"to":end_s}, timeout=30)
    r.raise_for_status()
    j = r.json()
    dfp = pd.DataFrame(j.get("prices", []), columns=["t","close"]).set_index("t")
    dfv = pd.DataFrame(j.get("total_volumes", []), columns=["t","volume"]).set_index("t")
    df = pd.concat([dfp, dfv], axis=1)
    df.index = pd.to_datetime(df.index, unit="ms", utc=True)
    df["open"] = df["close"]; df["high"] = df["close"]; df["low"] = df["close"]
    return df[["open","high","low","close","volume"]]

#our main entry point 
def load_ohlcv(symbol: str, timeframe: str, bars: int = 720, exchange_id="binance"):
    assert timeframe in VALID_TFS
    tf_minutes = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"6h":360,"12h":720,"1d":1440}[timeframe]
    lookback_minutes = tf_minutes * (bars + 5)
    since_ms = int((datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).timestamp()*1000)
    pair = f"{symbol}/USDT"
    try:
        df = ccxt_ohlcv(pair, exchange_id=exchange_id, timeframe=timeframe, since_ms=since_ms, limit=bars+50)
    except Exception:
        end_s = int(time.time()); start_s = end_s - lookback_minutes*60
        df = cg_market_chart_range(symbol, start_s=start_s, end_s=end_s)

    # map timeframe to pandas resample rule (use offset aliases)
    rule_map = {
        "1m": "1T", "3m": "3T", "5m": "5T", "15m": "15T", "30m": "30T",
        "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D"
    }
    rule = rule_map.get(timeframe, "1D")

    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    l = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    v = df["volume"].resample(rule).sum()
    out = pd.concat([o,h,l,c,v], axis=1).dropna()
    out.columns = ["open","high","low","close","volume"]
    return out.tail(bars)

