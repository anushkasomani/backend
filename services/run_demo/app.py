import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pprint import pprint
from services.planner.plan_analyzer import build_plan_json_from_text, analyze_features, build_plan_with_gemini, classify_intent, parse_scan_query
from services.data.data_layer import top_tickers_from_coingecko
from services.data.data_layer import load_universe
from trade_patterns.signals.scanner import scan
from trade_patterns.signals.render_helpers import render_cards_to_base64
from trade_patterns.data.ohlcv_intraday import load_ohlcv as tp_load_ohlcv
from services.data.sentiment import fetch_headlines, rolling_sentiment
from engine.engine import Plan
from engine.backtest import run_backtest
from dotenv import load_dotenv
import pandas as pd
from fastapi.middleware.cors import CORSMiddleware


load_dotenv()
auth_token = os.getenv("CP_AUTH_TOKEN")

app = FastAPI(title="Run Demo API")
origins=[
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:3000",
    "http://127.0,0.1:3000",
    "*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




class PlanRequest(BaseModel):
    text: str
    render: bool = True
    max_render: int = 3
    png_width: int = 900
    png_height: int = 500


class BacktestReq(BaseModel):
    plan: dict
    start: str | None = None
    end: str | None = None
    cp_key: str | None = None
    debug: bool = False


def to_plan_obj(pj: dict) -> Plan:
    return Plan(
        regime=pj["regime"],
        direction_bias=pj.get("direction_bias", "neutral"),
        universe=pj["universe_list"],
        gates=pj["gates"],
        custom_rules=pj.get("custom_rules", []),
        weighting=pj["weighting"],
        rebalance=pj["rebalance"],
        risk=pj["risk"],
        execution=pj["execution"],
        sentiment_cfg=pj.get("sentiment_cfg", {}),
    )


@app.post("/plan")
def get_plan(req: PlanRequest):
    """Convert user text into Plan JSON using existing helper (no Gemini)."""
    try:
        intent = classify_intent(req.text)
        # If intent is a scan request, run the scanner and return cards (with embedded PNGs)
        if intent == "scan":
            scan_q = parse_scan_query(req.text)
            # If user asked generically for trending/spike tokens (no explicit symbols),
            # prefer GeckoTerminal trending pools for real-time discovery.
            from services.data.geckoterminal import trending_pools
            # Decide if the user asked for generic market discovery. Rely on the
            # parser's `symbols_explicit` flag instead of fragile substring checks
            # so we correctly treat prompts like "tokens which spiked within 5m".
            is_generic_symbols = not bool(scan_q.get("symbols_explicit", False))
            if scan_q.get("filters", {}).get("recent_breakout") and is_generic_symbols:
                # fetch trending pools for the requested timeframe
                duration = scan_q.get("tf", "5m")
                pools = trending_pools(duration=duration)
                # return the raw pool attributes for now; mapping to symbols/ohlcv is left for future
                pools_simplified = []
                for p in pools:
                    attrs = p.get("attributes", {})
                    pools_simplified.append({
                        "id": p.get("id"),
                        "network": p.get("relationships", {}).get("network", {}).get("data", {}).get("id"),
                        "name": attrs.get("name"),
                        "price_change_percentage": attrs.get("price_change_percentage", {}),
                        "volume_usd": attrs.get("volume_usd", {}),
                        "reserve_in_usd": attrs.get("reserve_in_usd"),
                        "pool_created_at": attrs.get("pool_created_at")
                    })
                # Skipping OHLCV fetch from GeckoTerminal and returning simplified pool data directly.
                return {"intent": "scan_trending", "duration": duration, "cards": pools_simplified}
            
            # if user didn't specify symbols explicitly, perform market discovery
            if not scan_q.get("symbols") or not scan_q.get("symbols_explicit"):
                # fetch top tickers by volume and use top N (limit)
                try:
                    top = top_tickers_from_coingecko(per_page=50)
                    # Define and filter out common stablecoins to prevent errors
                    stablecoins = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD"}
                    filtered_top = [t for t in top if t.upper() not in stablecoins]
                    scan_q["symbols"] = filtered_top[: scan_q.get("limit", 12)]
                except Exception:
                    scan_q["symbols"] = ["BTC","ETH","SOL","BNB"]

            # call the scanner — map 'filters' returned by parser to the scan() parameter names
            filt = scan_q.get("filters") or {}
            indicator_filters = filt.get("indicators") if isinstance(filt, dict) else None
            recent_breakout_flag = bool(filt.get("recent_breakout", False))
            recency_bars = int(filt.get("recency_bars", 5))

            cards_block = scan(
                symbols=scan_q.get("symbols"),
                tf=scan_q.get("tf"),
                patterns=scan_q.get("patterns"),
                indicator_filters=indicator_filters,
                recent_breakout_flag=recent_breakout_flag,
                recency_bars=recency_bars,
                bars=scan_q.get("bars", 720),
                sort=scan_q.get("sort", "prob"),
                limit=scan_q.get("limit", 12)
            )
            # optionally render PNGs for top results using shared helper
            if req.render and cards_block.get("cards"):
                render_cards_to_base64(cards_block.get("cards", []), max_render=req.max_render,
                                       png_width=req.png_width, png_height=req.png_height,
                                       default_tf=scan_q.get("tf"), bars=scan_q.get("bars", 720))
            return {"intent": "scan", "query": scan_q, "cards": cards_block.get("cards", [])}

        # otherwise treat as plan-generation intent
        plan_json = build_plan_with_gemini(req.text)
        analysis = analyze_features(plan_json)
        return {"intent": "plan", "plan": plan_json, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/get_headlines")
def get_headlines():
    try:
        cp = fetch_headlines(auth_token)
        if isinstance(cp, pd.DataFrame):
            return {"headlines": cp.reset_index(drop=True).to_dict(orient="records")}
        return {"headlines": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/backtest")
def backtest(req: BacktestReq):
    try:
        meta = analyze_features(req.plan)
        assets = meta["assets"]
        days = meta["lookback_days"]
        ohlcv = load_universe(assets, since_days=days)
        cp = fetch_headlines(auth_token if req.cp_key is None else req.cp_key)
        sent = rolling_sentiment(cp) if isinstance(cp, pd.DataFrame) and not cp.empty else {}

        plan_obj = to_plan_obj(req.plan)
        ec, stats = run_backtest(plan_obj, ohlcv, sent, start=req.start, end=req.end)

        # Handle both DataFrame and dict output for ec
        equity_curve = []
        if isinstance(ec, dict) and "equity" in ec and isinstance(ec["equity"], dict):
            for t, v in ec["equity"].items():
                equity_curve.append({"t": str(t), "equity": float(v)})
        elif hasattr(ec, "index") and hasattr(ec, "__getitem__"):
            # Assume ec is a DataFrame with an 'equity' column
            for t, v in zip(ec.index, ec["equity"]):
                equity_curve.append({"t": str(t), "equity": float(v)})

        return {"stats": stats, "equity_curve": equity_curve}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    # keep previous script behavior when run directly for convenience
    user_text = "I'm bullish on BTC and ETH. You choose the best technicals with sentiment good."
    plan_json = build_plan_with_gemini(user_text)
    print("\n--- Plan JSON ---")
    pprint(plan_json)

    feats = analyze_features(plan_json)
    ohlcv = load_universe(feats["assets"], since_days=feats["lookback_days"])
    news = fetch_headlines(os.getenv("CP_AUTH_TOKEN"))
    sent = rolling_sentiment(news) if not news.empty else {}

    plan = to_plan_obj(plan_json)
    recent = {a: df.tail(250) for a, df in ohlcv.items()}
    # best-effort: call target_weights if available, else skip explain
    try:
        from engine.engine import target_weights, build_trade_plan
        tw, explains = target_weights(plan, recent, sent)
        print("\n--- Target Weights ---")
        pprint(tw)
        print("\n--- Explain ---")
        pprint(explains)
    except Exception:
        print("target_weights not available or raised an error; skipping explain")

    ec, stats = run_backtest(plan, ohlcv, sent, start="2024-01-01")
    print("\n--- Backtest Stats ---")
    pprint(stats)
    try:
        print("\n--- Equity tail ---")
        print(ec.tail())
    except Exception:
        pass