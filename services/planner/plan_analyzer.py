# import re
# from typing import Dict, List

# MAX_LOOKBACK = 540

# def analyze_plan(plan: Dict) -> Dict:
#     assets = plan["universe"]
#     feats: List[str] = []

#     def add_feat(tok: str):
#         if tok not in feats: feats.append(tok)

#     patt_sma = re.compile(r"SMA\((\d+)(?:,VOLUME)?\)")
#     patt_ema = re.compile(r"EMA\((\d+)\)")
#     gates = plan.get("gates", {}).get("all_of", []) + plan.get("gates", {}).get("any_of", [])
#     for g in gates:
#         expr = g["expr"].upper()
#         if "SMA(" in expr:
#             for n in patt_sma.findall(expr):
#                 if ",VOLUME" in expr: add_feat(f"VOL_SMA_{n}")
#                 else: add_feat(f"SMA_{n}")
#         if "EMA(" in expr:
#             for n in patt_ema.findall(expr):
#                 add_feat(f"EMA_{n}")
#         if "RSI(14)" in expr: add_feat("RSI_14")
#         if "RET_60D" in expr: add_feat("RET_60D")
#         if "SENTIMENT" in expr: add_feat("SENTIMENT")

#     # derive lookback: max window + 20% buffer (min 90)
#     windows = []
#     for f in feats:
#         if f.startswith("SMA_") or f.startswith("EMA_") or f.startswith("VOL_SMA_"):
#             windows.append(int(f.split("_")[-1]))
#         if f == "RET_60D": windows.append(60)
#         if f == "RSI_14": windows.append(14)
#     need = max(windows) if windows else 60
#     need = int(min(MAX_LOOKBACK, max(90, need * 1.2)))

#     return {"assets": assets, "features": feats, "lookback_days": need}

# #aren't we replacing sentiment with thresholds in plan_analyzer?

import re
from engine.engine import Plan
from typing import List, Dict, Any
from dotenv import load_dotenv
import os
import google.generativeai as genai
import json
load_dotenv()

api_key= os.getenv("GOOGLE_API_KEY")

DEFAULT_JSON = {
  "name": "Auto_Expert",
  "regime": "auto",
  "direction_bias": "neutral",
  "universe_list": ["BTC","ETH","SOL"],
  "custom_rules": [],
  "gates": {
    "trend": {"ema_short":50,"ema_long":200,"adx_min":20},
    "range": {"adx_max":20,"bb_bw_pct_max":0.30},
    "breakout": {"donchian_n":20,"min_vol_mult":1.5,"adx_rising":True},
    "support": {"atr_mult":0.8,"rsi_min":40},
    "sentiment":"AUTO"
  },
  "weighting": {
    "mode":"composite",
    "coeffs":{"trend":0.35,"momentum":0.35,"volume":0.15,"sentiment":0.15},
    "tilt_sentiment_pct":0.10
  },
  "rebalance":{"cadence":"weekly","band_pp":5.0,"turnover_max":0.15},
  "risk":{"max_weight":0.40,"hard_cap":0.50,"slippage_max_bps":80,"order_max_usd":2000,"cooldown_hours":6},
  "execution":{"chunk_usd":2000,"use_yield":False},
  "sentiment_cfg":{"good_threshold":0.30,"bad_threshold":-0.30,"shock_delta_24h":0.50}
}


def build_plan_json_from_text(user_text: str) -> dict:
    t = user_text.lower()
    plan = {**DEFAULT_JSON}
    # universe
    uni = []
    for k in ["btc","eth","sol"]:
        if re.search(rf"\b{k}\b", t): uni.append(k.upper())
    if uni: plan["universe_list"] = uni
    # direction bias
    if "bullish" in t: plan["direction_bias"] = "bullish"
    elif "bearish" in t: plan["direction_bias"] = "bearish"
    # regime hints
    for r in ["trend","range","breakout","support"]:
        if r in t: plan["regime"] = r
    # explicit rules
    rules = []
    if "price > 30d ma" in t or "above 30d" in t:
        rules.append("CLOSE>SMA(30)")
    m = re.search(r"rsi\s*>\s*([4-9]\d)", t)  # FIXED: correct whitespace and capture
    if m:
        rules.append(f"RSI(14)>{m.group(1)}")
    if "volume strong" in t:
        rules.append("VOLUME>1.5*VOL_SMA(20)")
    if "sentiment good" in t:
        rules.append("SENTIMENT>=GOOD")
    plan["custom_rules"] = rules
    return plan

def build_plan_with_gemini(user_text: str) -> dict:
    """
    Use Gemini to generate a structured Plan JSON.
    Falls back to regex parser if Gemini fails.
    Ensures return format matches DEFAULT_JSON.
    """
    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        print(f"Gemini config error: {e}")
        return build_plan_json_from_text(user_text)

    # Instead of Plan.model_json_schema(), just show Gemini the expected keys
    prompt_parts = [
        "You are an expert crypto trading strategist. "
        "The user will provide a natural language description of a strategy. "
        "Your job is to fill out a Plan JSON object that strictly conforms to the schema below. "
        "If the user does not mention some fields, auto-populate them with robust defaults "
        "from technical analysis and risk management. "
        "Use trade direction bias strictly as given in the user text example 'bearish', 'bullish' otherwise default to 'neutral'."
        "The universe list must contain only the asset mentioned in the user text.The universe list mentioned in the default json is just an example."
        "Never leave required fields as null or None. "
        "\n\nJSON Schema Example:\n"
        f"{json.dumps(DEFAULT_JSON, indent=2)}\n\n"
        f"User's Strategy Description: \"{user_text}\"\n\n"
        "Output only the JSON object, no explanations."
    ]

    model = genai.GenerativeModel('gemini-2.0-flash')

    try:
        response = model.generate_content(
            prompt_parts,
            generation_config={"response_mime_type": "application/json"}
        )
        raw_json_output = response.text
        plan_data = json.loads(raw_json_output)

        # Merge Gemini output with DEFAULT_JSON (to ensure consistent keys)
        merged_plan = {**DEFAULT_JSON, **plan_data}
        return merged_plan

    except Exception as e:
        print(f"Gemini generation error: {e}")
        # Fallback: regex parser
        return build_plan_json_from_text(user_text)


    model = genai.GenerativeModel('gemini-2.0-flash')

    try:
        response = model.generate_content(
            prompt_parts,
            generation_config={"response_mime_type": "application/json"}
        )
        raw_json_output = response.text
        plan_data = json.loads(raw_json_output)

        # Validate against Pydantic Plan schema
        validated_plan = Plan.model_validate(plan_data)
        return validated_plan.model_dump()

    except Exception as e:
        # Fallback: regex parser
        return build_plan_json_from_text(user_text)


def analyze_features(plan_json: dict) -> dict:
    feats: List[str]= []
    def add_feats(tok:str):
        if tok not in feats: feats.append(tok)
    return {
        "assets": plan_json["universe_list"],
        "features": ["OHLCV","SMA/EMA","RSI/StochRSI","MACD","ADX/DI","ATR","Bollinger/Keltner","Donchian","OBV/CMF/MFI","VWAP","Sentiment"],
        "lookback_days": 540
    }


SCAN_PATTERNS = {
    "head and shoulders":"head_shoulders",
    "inverse head and shoulders":"inverse_head_shoulders",
    "ascending triangle":"ascending_triangle",
    "descending triangle":"descending_triangle",
    "symmetrical triangle":"symmetrical_triangle",
    "bull flag":"bull_flag",
    "bear flag":"bear_flag",
    "double top":"double_top",
    "double bottom":"double_bottom",
    "rising wedge":"wedge_rising",
    "falling wedge":"wedge_falling",
    "doji":"doji","hammer":"hammer",
    "engulfing":"engulfing_bull"
}

def parse_scan_query(text: str) -> Dict[str, Any] | None:
    T = text.lower()
    tf = "5m"
    for k in ["1m","3m","5m","15m","30m","1h","2h","4h","6h","12h","1d"]:
        if re.search(rf"\b{k}\b", T):
            tf = k; break
    pats = set()
    for phrase, key in SCAN_PATTERNS.items():
        if phrase in T:
            pats.add(key)
    if "head & shoulders" in T or "h&s" in T:
        pats.add("head_shoulders")
    if "inverse h&s" in T:
        pats.add("inverse_head_shoulders")
    if "breakout" in T:
        pats.update(["ascending_triangle","symmetrical_triangle","bull_flag"])
    if not pats:
        pats = set(["ascending_triangle","symmetrical_triangle","bull_flag","double_bottom","wedge_falling",
                    "head_shoulders","double_top","bear_flag","wedge_rising"])
    indicators = []
    m = re.search(r"rsi\s*\(?14\)?\s*([<>]=?)\s*(\d+)", T)
    if m: indicators.append(f"RSI(14){m.group(1)}{m.group(2)}")
    if "ema 50 > ema 200" in T or "golden cross" in T:
        indicators.append("EMA(50)>EMA(200)")
    if "ema 50 < ema 200" in T or "death cross" in T:
        indicators.append("EMA(50)<EMA(200)")
    if "above 30d" in T or "price > 30d ma" in T:
        indicators.append("CLOSE>SMA(30)")
    # treat words like spike/trending as an explicit breakout/volume signal
    recent_breakout_flag = any(k in T for k in ["breakout", "just broke", "recently broke", "spike", "spiked", "spiking", "volume spike", "trending", "trend"]) 
    symbols = []
    # broaden default tickers for discovery
    tickers = ["btc","eth","sol","bnb","xrp","ada","avax","ltc","link","matic","doge","shib","pepe","wif","bonk","floki","brett","sui","near","apt","vet","eos"]
    for k in tickers:
        if re.search(rf"\b{k}\b", T): symbols.append(k.upper())
    symbols_explicit = bool(symbols)
    # do not inject hardcoded defaults here; let the caller decide discovery
    # if no symbols were mentioned the caller can fetch top tickers from market data
    limit = 12
    m = re.search(r"top\s+(\d+)", T)
    if m: limit = int(m.group(1))
    # If the user asked about spikes/trending, add a volume filter to capture volume pickups
    if recent_breakout_flag and "VOLUME>" not in " ".join(indicators):
        indicators.append("VOLUME>1.5*VOL_SMA(20)")

    return {
        "symbols": symbols,
        "symbols_explicit": symbols_explicit,
        "tf": tf,
        "patterns": list(pats),
        "filters": {"indicators": indicators, "recent_breakout": recent_breakout_flag, "recency_bars": 5},
        "sort": "prob",
        "limit": limit,
        "bars": 720
    }

def classify_intent(text: str) -> str:
    """Classify whether the user's free-text is asking for a "scan" (search/visualize) or a
    full "plan" (strategy specification). This uses a small scoring heuristic so that
    ambiguous technical terms (eg. "EMA") don't force a scan when the user really means
    to describe a plan.
    """
    T = text.lower()

    # Keywords that indicate a scan/query intent (look for tokens/patterns/timeframes)
    scan_kw = [
        "find","show","list","which","scan","search","find tokens","show tokens",
    "breakout","forming","just broke","recently broke","volume spike","volume pickup",
    "give","give me","latest",
        "top","rank","best", "where"
    ]

    # Keywords that indicate the user is asking for a Plan / strategy spec
    plan_kw = [
        "trade","plan","when to","strategy","allocate","allocate to","weight","rebalance",
        "buy","sell","entry","stop","take profit","tp","sl","risk","risk management",
        "regime","tilt","execution","weighting","portfolio","position sizing"
    ]

    # Ambiguous technical tokens that alone should not force a scan
    tech_kw = ["rsi","ema","sma","ma","bollinger","adx","macd","atr","vwap"]

    score_scan = 0
    score_plan = 0

    for k in scan_kw:
        if k in T: score_scan += 2
    for k in plan_kw:
        if k in T: score_plan += 3
    for k in tech_kw:
        if k in T: score_plan += 1  # treat technical terms as mild plan signal unless other scan cues exist

    # If text starts with an interrogative or contains a question mark, bias to scan
    if T.strip().startswith(tuple(["find","show","which","what","list","give"])) or "?" in T:
        score_scan += 2

    # Presence of an explicit timeframe generally indicates a scan request (eg. "on 1h", "5m")
    if re.search(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d)\b", T):
        score_scan += 2

    # Final tie-breaker: prefer plan when scores are equal (so mentioning EMA in a plan won't flip to scan)
    if score_plan >= score_scan:
        return "plan"
    return "scan"
