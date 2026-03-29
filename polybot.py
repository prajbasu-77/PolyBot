# ============================================================
#   POLYBOT v2.0 — Polymarket Maker Strategy Bot
#   Runs in background, feeds data to dashboard at :8888
# ============================================================
import os, json, time, asyncio, threading, requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

PRIVATE_KEY    = os.getenv("PRIVATE_KEY", "")
FUNDER         = os.getenv("POLYMARKET_FUNDER", "")
PAPER_MODE     = os.getenv("PAPER_MODE", "TRUE").upper() == "TRUE"
BET_SIZE       = float(os.getenv("BET_SIZE", "5.0"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.85"))
MAKER_PRICE    = float(os.getenv("MAKER_PRICE", "0.92"))
LOG_FILE       = os.path.join(os.path.dirname(__file__), "trades_log.json")
GAMMA_API      = "https://gamma-api.polymarket.com"
HOST           = "https://clob.polymarket.com"

state = {
    "running": False, "paper_mode": PAPER_MODE,
    "balance": 0.0, "paper_balance": 500.0,
    "total_profit": 0.0, "total_trades": 0,
    "wins": 0, "losses": 0, "win_rate": 0.0,
    "btc_price": 0.0, "eth_price": 0.0,
    "btc_change": 0.0, "eth_change": 0.0,
    "current_market": "Starting scanner...",
    "current_signal": "WAITING", "signal_confidence": 0.0,
    "active_orders": [], "trades": [], "log": [],
    "markets_scanned": 0, "scan_interval": 15,
    "last_scan": "--", "next_scan": "--",
    "candles": [], "uptime_seconds": 0,
    "momentum_5m": 0.0, "momentum_1m": 0.0,
}

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"time": ts, "level": level, "msg": msg}
    state["log"].insert(0, entry)
    state["log"] = state["log"][:300]
    print(f"[{ts}][{level}] {msg}")

def save_trades():
    try:
        with open(LOG_FILE, "w") as f:
            json.dump(state["trades"][:500], f, indent=2)
    except Exception as e:
        log(f"Save error: {e}", "ERROR")

def load_trades():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                state["trades"] = json.load(f)
            wins   = sum(1 for t in state["trades"] if t.get("result") == "WIN")
            losses = sum(1 for t in state["trades"] if t.get("result") == "LOSS")
            profit = sum(t.get("pnl", 0) for t in state["trades"])
            total  = wins + losses
            state["wins"] = wins; state["losses"] = losses
            state["total_trades"] = len(state["trades"])
            state["total_profit"] = round(profit, 4)
            state["win_rate"] = round((wins/total*100) if total > 0 else 0, 1)
        except: pass

def get_client():
    if not PRIVATE_KEY or "YOUR_" in PRIVATE_KEY:
        return None
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON
        client = ClobClient(host=HOST, key=PRIVATE_KEY, chain_id=POLYGON,
                            signature_type=0, funder=FUNDER)
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except Exception as e:
        log(f"Client error: {e}", "ERROR")
        return None

def fetch_candles(symbol="BTC", interval="1m", limit=10):
    try:
        pair = "BTCUSDT" if symbol == "BTC" else "ETHUSDT"
        r = requests.get("https://api.binance.com/api/v3/klines",
            params={"symbol": pair, "interval": interval, "limit": limit}, timeout=5)
        raw = r.json()
        candles = []
        for c in raw:
            candles.append({
                "time": datetime.fromtimestamp(c[0]/1000).strftime("%H:%M"),
                "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]),
                "volume": float(c[5])
            })
        return candles
    except: return []

def analyze_momentum():
    try:
        r = requests.get("https://api.binance.com/api/v3/klines",
            params={"symbol":"BTCUSDT","interval":"1m","limit":15}, timeout=5)
        candles = r.json()
        closes  = [float(c[4]) for c in candles]
        current = closes[-1]
        prev5   = closes[-6]
        prev1   = closes[-2]
        state["btc_price"]    = current
        state["momentum_5m"]  = round((current - prev5) / prev5 * 100, 4)
        state["momentum_1m"]  = round((current - prev1) / prev1 * 100, 4)
        state["candles"]      = fetch_candles("BTC", "1m", 30)
        m5 = state["momentum_5m"]
        m1 = state["momentum_1m"]
        if m5 > 0.04 and m1 > 0:
            conf = min(0.50 + abs(m5) * 8, 0.99)
            return "UP", round(conf, 3)
        elif m5 < -0.04 and m1 < 0:
            conf = min(0.50 + abs(m5) * 8, 0.99)
            return "DOWN", round(conf, 3)
        else:
            return "FLAT", 0.50
    except Exception as e:
        log(f"Momentum error: {e}", "WARN")
        return "FLAT", 0.0

def scan_markets():
    try:
        r = requests.get(f"{GAMMA_API}/markets", params={
            "active":"true","closed":"false","limit":100,
            "order":"end_date_min","ascending":"true"}, timeout=10)
        markets = r.json()
        results = []
        kw = ["bitcoin","btc","ethereum","eth","15-minute","15 minute","next 15","crypto"]
        for m in markets:
            title = (m.get("question") or m.get("title") or "").lower()
            if any(k in title for k in kw):
                if any(k in title for k in ["up","down","higher","lower","above","below","rise","fall"]):
                    results.append(m)
        state["markets_scanned"] = len(results)
        return results
    except Exception as e:
        log(f"Scan error: {e}", "WARN")
        return []

def get_odds(market):
    try:
        tokens = market.get("tokens") or []
        if len(tokens) < 2: return None, None, 0.5, 0.5
        yes_tok = next((t for t in tokens if "yes" in (t.get("outcome") or "").lower()), tokens[0])
        no_tok  = next((t for t in tokens if "no"  in (t.get("outcome") or "").lower()), tokens[1])
        return (yes_tok.get("token_id"), no_tok.get("token_id"),
                float(yes_tok.get("price") or 0.5), float(no_tok.get("price") or 0.5))
    except: return None, None, 0.5, 0.5

def place_order(client, token_id, side, price, size, title, direction):
    import random
    trade = {
        "id": int(time.time()*1000),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market": title[:70], "direction": direction,
        "side": side, "price": price, "size": round(size, 4),
        "cost": round(price * size, 4), "paper": PAPER_MODE,
        "status": "OPEN", "result": "PENDING", "pnl": 0.0, "order_id": ""
    }
    if PAPER_MODE:
        trade["order_id"] = f"PAPER-{trade['id']}"
        trade["status"]   = "FILLED"
        state["paper_balance"] = round(state["paper_balance"] - trade["cost"], 4)
        state["total_trades"] += 1
        log(f"PAPER BET — {direction} | ${trade['cost']:.2f} | {title[:40]}", "TRADE")
    else:
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            resp = client.create_and_post_order(args, OrderType.GTC)
            trade["order_id"] = resp.get("orderID","?")
            state["total_trades"] += 1
            log(f"LIVE ORDER — {direction} | ${trade['cost']:.2f} | ID:{trade['order_id']}", "TRADE")
        except Exception as e:
            log(f"Order failed: {e}", "ERROR")
            return
    state["active_orders"].append(trade)
    state["trades"].insert(0, trade)
    save_trades()
