from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests, json, time, threading, logging, redis
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from config import TS_USERNAME as USERNAME, TS_API_KEY as API_KEY

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

log_handler = RotatingFileHandler("fastapi_trading_bot.log", maxBytes=5*1024*1024, backupCount=3)
log_handler.setLevel(logging.INFO)
formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
log_handler.setFormatter(formatter)
logging.getLogger().addHandler(log_handler)

def log(message):
    try:
        clean_msg = str(message).encode("ascii", "ignore").decode()
    except:
        clean_msg = str(message)
    logging.info(clean_msg)
    print(clean_msg)


API_BASE = "https://api.topstepx.com/api"
DEFAULT_ORDER_SIZE = 1
DEFAULT_SL_PERCENT = 0.11
DEFAULT_TP_PERCENT = 0.22

executor = ThreadPoolExecutor(max_workers=200)
redis_conn = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

symbol_map = {
    "MES1!": "CON.F.US.MES.M25", "MNQ1!": "CON.F.US.MNQ.M25", "MYM1!": "CON.F.US.MYM.M25",
    "M2KM25": "CON.F.US.M2K.M25", "ESM25": "CON.F.US.ES.M25", "NQM25": "CON.F.US.NQ.M25",
    "YMM25": "CON.F.US.YM.M25", "RTYM25": "CON.F.US.RTY.M25", "MCLN25": "CON.F.US.MCL.M25",
    "CL1!": "CON.F.US.CLE.M25", "CLN25": "CON.F.US.CLE.M25", "GC1!": "CON.F.US.MGC.M25",
    "MGC1!": "CON.F.US.MGC.M25", "SILN25": "CON.F.US.SI.M25", "GCM25": "CON.F.US.GC.M25",
    "PLN25": "CON.F.US.PL.M25", "MHGN25": "CON.F.US.MHG.M25", "HGN25": "CON.F.US.HG.M25",
    "MNGM25": "CON.F.US.MNG.M25", "NGM25": "CON.F.US.NG.M25", "RBN25": "CON.F.US.RB.M25",
    "HEM25": "CON.F.US.HE.M25", "LEQ25": "CON.F.US.LE.M25", "HON25": "CON.F.US.HO.M25",
    "METK25": "CON.F.US.MET.M25", "MBTK25": "CON.F.US.MBT.M25", "6AM25": "CON.F.US.6A.M25",
    "6BM25": "CON.F.US.6B.M25", "6CM25": "CON.F.US.6C.M25", "6EM25": "CON.F.US.6E.M25",
    "6JM25": "CON.F.US.6J.M25", "6MM25": "CON.F.US.6M.M25", "6NM25": "CON.F.US.6N.M25",
    "6SM25": "CON.F.US.6S.M25", "M6AM25": "CON.F.US.M6A.M25", "M6BM25": "CON.F.US.M6B.M25",
    "M6EM25": "CON.F.US.M6E.M25", "UBM25": "CON.F.US.UB.M25", "TNM25": "CON.F.US.TN.M25",
    "ZBM25": "CON.F.US.ZB.M25", "ZFM25": "CON.F.US.ZF.M25", "ZNM25": "CON.F.US.ZN.M25",
    "ZTM25": "CON.F.US.ZT.M25", "ZCN25": "CON.F.US.ZC.M25", "ZWN25": "CON.F.US.ZW.M25",
    "ZSN25": "CON.F.US.ZS.M25", "ZLN25": "CON.F.US.ZL.M25", "ZMN25": "CON.F.US.ZM.M25",
    "SIN25": "CON.F.US.SI.M25", "E7M25": "CON.F.US.E7.M25", "QGM25": "CON.F.US.QG.M25",
    "NKDM25": "CON.F.US.NKD.M25"
}

def now_ms(): return int(time.time() * 1000)

def is_duplicate(symbol, account_id):
    key = f"dupe:{symbol}_{account_id}"
    if redis_conn.exists(key): return True
    redis_conn.setex(key, 5, 1)
    return False

def get_token():
    cached = redis_conn.get("projectx:token")
    if cached:
        return cached
    try:
        r = requests.post(f"{API_BASE}/Auth/loginKey", json={"userName": USERNAME, "apiKey": API_KEY}, headers={"Content-Type": "application/json"}, timeout=5)
        token = r.json().get("token") if r.status_code == 200 else None
        if token:
            redis_conn.setex("projectx:token", 82800, token)  # cache for 23 hours
        return token
    except Exception as e:
        log(f"Auth Error: {e}")
        return None

def authenticated_post(url, payload):
    token = get_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/plain"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        if r.status_code == 401:
            redis_conn.delete("projectx:token")  # force refresh
            token = get_token()
            headers["Authorization"] = f"Bearer {token}"
            r = requests.post(url, json=payload, headers=headers, timeout=5)
        return r
    except Exception as e:
        log(f"Authenticated POST Error: {e}")
        return None

def get_contract_details(contract_id, token):
    try:
        r = requests.get(f"{API_BASE}/Contract/searchById?id={contract_id}", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log(f"Contract Detail Fetch Error: {e}")
    return {}

def align_to_tick(price, tick_size):
    return round(round(price / tick_size) * tick_size, 6)

def close_position(account_id, contract_id):
    url = f"{API_BASE}/Position/closeContract"
    payload = {"accountId": int(account_id), "contractId": str(contract_id)}
    response = authenticated_post(url, payload)
    if response:
        log(f"[{account_id}] Full Close: {response.status_code} {response.text}")
        return response.status_code == 200 and "success" in response.text.lower()
    return False

def partial_close_position(account_id, contract_id, size):
    url = f"{API_BASE}/Position/partialCloseContract"
    payload = {"accountId": int(account_id), "contractId": str(contract_id), "size": int(size)}
    response = authenticated_post(url, payload)
    if response:
        log(f"[{account_id}] Partial Close: {response.status_code} {response.text}")
        return response.status_code == 200 and "success" in response.text.lower()
    return False

def has_open_order(account_id, contract_id, token):
    try:
        r = requests.post(f"{API_BASE}/Order/search", json={"accountId": account_id, "contractId": contract_id, "isOpen": True}, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        return r.status_code == 200 and len(r.json().get("orders", [])) > 0
    except: return False

def execute_trade(payload):
    try:
        raw_symbol = payload.get("symbol")
        if not raw_symbol:
            log("Missing 'symbol' in payload")
            return
        contract_id = symbol_map.get(raw_symbol, raw_symbol)
        token = get_token()
        if not token:
            log("Token fetch failed")
            return

        action = payload.get("action", "").lower()
        if action == "close":
            return close_position(payload.get("accountId"), contract_id)
        if action == "partial_close":
            return partial_close_position(payload.get("accountId"), contract_id, int(payload.get("size", 1)))

        price = payload.get("price")
        quantity = payload.get("quantity")
        data = payload.get("data")

        if price is None or quantity is None or data is None:
            contract_data = get_contract_details(contract_id, token)
            price = contract_data.get("lastPrice")
            quantity = quantity or DEFAULT_ORDER_SIZE
            data = data or "long"
            if price is None:
                log(f"Missing required field(s) in payload: price={price}, quantity={quantity}, data={data}")
                return

        side = 0 if "long" in data.lower() else 1
        price = float(price)
        qty = float(quantity)

        def safe_float(val, default=0.0):
            try: return float(val)
            except (TypeError, ValueError): return default

        def safe_int(val, default=1):
            try: return int(val)
            except (TypeError, ValueError): return default

        partial_close = payload.get("partial_close", {})
        full_close = payload.get("full_close", {})

        tp1_rr = safe_float(partial_close.get("target_rr"), 1.0)
        tp2_rr = safe_float(full_close.get("target_rr"), 2.0)
        tp3_rr = 3.0
        tp4_rr = 4.0
        tp5_rr = 5.0
        partial_size = safe_int(partial_close.get("size"), 1)

        for acc in payload.get("multiple_accounts", []):
            acc_id = acc["account_id"]
            full_qty = int(qty * float(acc.get("quantity_multiplier", 1)))
            if is_duplicate(contract_id, acc_id): continue
            if has_open_order(acc_id, contract_id, token): continue

            order_type = int(payload.get("type", 2))
            trail_price = payload.get("trailPrice")

            order_payload = {
                "accountId": acc_id,
                "contractId": contract_id,
                "type": order_type,
                "side": side,
                "size": full_qty
            }

            if order_type == 5:
                if trail_price is None:
                    contract_data = get_contract_details(contract_id, token)
                    trail_price = float(contract_data.get("lastPrice", price))
                tick_size = safe_float(get_contract_details(contract_id, token).get("tickSize"), 0.01)
                order_payload["trailPrice"] = align_to_tick(float(trail_price), tick_size)

            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            try:
                r = requests.post(f"{API_BASE}/Order/place", json=order_payload, headers=headers, timeout=5)
                log(f"[{acc_id}] Place Order (type {order_type}): {r.status_code} {r.text}")
                if r.status_code == 200 and r.json().get("success"):
                    if order_type != 5:
                        direction = 1 if side == 0 else -1
                        tp1 = round(price + direction * price * (tp1_rr * 0.01), 2)
                        tp2 = round(price + direction * price * (tp2_rr * 0.01), 2)
                        tp3 = round(price + direction * price * (tp3_rr * 0.01), 2)
                        tp4 = round(price + direction * price * (tp4_rr * 0.01), 2)
                        tp5 = round(price + direction * price * (tp5_rr * 0.01), 2)
                        redis_conn.set(f"open:{contract_id}", json.dumps({
                            "price": price, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                            "tp4": tp4, "tp5": tp5,
                            "side": side, "account_id": acc_id,
                            "partial_size": partial_size,
                            "partial_closed": False,
                            "full_closed": False
                        }))
                    log(f"Order placed and targets set for {contract_id} - {acc_id}")
            except Exception as e:
                log(f"Place Order Error: {e}")

    except Exception as e:
        log(f"execute_trade error: {e}")

@app.post("/webhook")
async def webhook(request: Request):
    try:
        payload = await request.json()
        log(f"Incoming Payload: {payload}")
        executor.submit(execute_trade, payload)
        return {"status": "executing"}
    except Exception as e:
        raw_body = await request.body()
        log(f"Webhook Error: {e} | Raw: {raw_body}")
        return JSONResponse(status_code=400, content={"error": "Invalid payload", "details": str(e)})


@app.get("/healthcheck")
async def healthcheck():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/token")
def get_auth_token():
    token = get_token()
    if token:
        return {"token": token}
    else:
        return JSONResponse(status_code=401, content={"error": "Authentication failed"})


@app.get("/dashboard", response_class=HTMLResponse)
async def home():
    keys = redis_conn.keys("open:*")
    trade_keys = redis_conn.lrange("trades:history", -10, -1)
    wins = int(redis_conn.get("stats:wins") or 0)
    losses = int(redis_conn.get("stats:losses") or 0)
    total_pnl = float(redis_conn.get("stats:pnl") or 0.0)
    open_trades = []

    for key in keys:
        try:
            data = redis_conn.get(key)
            if data:
                trade = json.loads(data)
                open_trades.append({
                    "symbol": key.split(":")[1],
                    "price": trade.get("price"),
                    "tp1": trade.get("tp1"),
                    "tp2": trade.get("tp2"),
                    "tp3": trade.get("tp3"),
                    "side": "LONG" if trade.get("side") == 0 else "SHORT",
                    "account_id": trade.get("account_id")
                })
        except Exception as e:
            log(f"Error reading {key}: {e}")

    total_trades = wins + losses
    win_rate = round((wins / total_trades) * 100, 2) if total_trades > 0 else 0.0

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="10">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Trading Bot Dashboard</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ padding: 30px; }}
            .table td, .table th {{ vertical-align: middle; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="mb-4">📊 FastAPI Trading Bot Dashboard</h1>
            <div class="row mb-4">
                <div class="col">
                    <div class="card text-bg-primary">
                        <div class="card-body">
                            <h5 class="card-title">Open Trades</h5>
                            <p class="card-text display-6">{len(open_trades)}</p>
                        </div>
                    </div>
                </div>
                <div class="col">
                    <div class="card text-bg-success">
                        <div class="card-body">
                            <h5 class="card-title">Win Rate</h5>
                            <p class="card-text display-6">{win_rate}%</p>
                        </div>
                    </div>
                </div>
                <div class="col">
                    <div class="card text-bg-warning">
                        <div class="card-body">
                            <h5 class="card-title">Total PnL</h5>
                            <p class="card-text display-6">${total_pnl:.2f}</p>
                        </div>
                    </div>
                </div>
            </div>

            <h3 class="mb-3">🟢 Open Positions</h3>
            <table class="table table-bordered table-hover">
                <thead class="table-light">
                    <tr>
                        <th>Symbol</th><th>Side</th><th>Entry</th><th>TP1</th><th>TP2</th><th>TP3</th><th>Account</th>
                    </tr>
                </thead>
                <tbody>
    """
    for t in open_trades:
        html += f"""
            <tr>
                <td>{t['symbol']}</td><td>{t['side']}</td><td>{t['price']}</td>
                <td>{t['tp1']}</td><td>{t['tp2']}</td><td>{t['tp3']}</td><td>{t['account_id']}</td>
            </tr>
        """

    html += """
                </tbody>
            </table>

            <h3 class="mt-5 mb-3">📜 Trade History</h3>
            <ul class="list-group mb-4">
    """

    for raw in reversed(trade_keys):
        html += f"<li class='list-group-item'>{raw}</li>"

    html += f"""
            </ul>
            <footer class="text-muted text-center mt-4">
                <small>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small>
            </footer>
        </div>
    </body>
    </html>
    """
    return html


# Call this after each closed position
def update_stats(symbol, pnl):
    redis_conn.lpush("trades:history", f"{symbol} - {'WIN' if pnl > 0 else 'LOSS'} - PnL: ${pnl:.2f}")
    redis_conn.incr("stats:wins" if pnl > 0 else "stats:losses")
    redis_conn.incrbyfloat("stats:pnl", pnl)

def get_open_position_size(account_id, contract_id):
    try:
        token = get_token()
        if not token:
            return 0
        r = requests.post(f"{API_BASE}/Position/search", json={"accountId": account_id}, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if r.status_code == 200:
            positions = r.json().get("positions", [])
            for pos in positions:
                if pos["contractId"] == contract_id:
                    return int(pos.get("size", 0))
    except Exception as e:
        log(f"Get Position Size Error: {e}")
    return 0


def monitor_take_profits(interval=5):
    def runner():
        while True:
            keys = redis_conn.keys("open:*")
            for key in keys:
                try:
                    trade = json.loads(redis_conn.get(key))
                    if trade.get("full_closed"): continue

                    contract_id = key.split(":")[1]
                    account_id = trade.get("account_id")
                    token = get_token()
                    if not token: continue

                    contract_data = get_contract_details(contract_id, token)
                    last_price = float(contract_data.get("lastPrice", 0))

                    size = get_open_position_size(account_id, contract_id)
                    if size <= 0: continue  # nothing to do

                    third = max(1, round(size / 3))

                    # TP1: Close 1/3
                    if not trade.get("tp1_closed") and last_price >= trade["tp1"]:
                        log(f"[{account_id}] TP1 HIT → Closing 1/3 ({third})")
                        if partial_close_position(account_id, contract_id, third):
                            trade["tp1_closed"] = True
                            redis_conn.set(key, json.dumps(trade))

                    # TP2: Close another 1/3
                    elif not trade.get("tp2_closed") and last_price >= trade["tp2"]:
                        log(f"[{account_id}] TP2 HIT → Closing 1/3 ({third})")
                        if partial_close_position(account_id, contract_id, third):
                            trade["tp2_closed"] = True
                            redis_conn.set(key, json.dumps(trade))

                    # TP3: Close remaining
                    elif not trade.get("tp3_closed") and last_price >= trade["tp3"]:
                        log(f"[{account_id}] TP3 HIT → Closing final portion ({size})")
                        if partial_close_position(account_id, contract_id, size):
                            trade["tp3_closed"] = True
                            trade["full_closed"] = True
                            redis_conn.set(key, json.dumps(trade))

                except Exception as e:
                    log(f"TP Monitor Error: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()

    
def keep_alive(interval=300):
    def ping():
        while True:
            log("✅ Keep-alive heartbeat ping.")
            time.sleep(interval)
    thread = threading.Thread(target=ping, daemon=True)
    thread.start()

def redis_keep_alive(interval=60):
    def ping_redis():
        while True:
            try:
                redis_conn.ping()
                log("✅ Redis connection OK")
            except Exception as e:
                log(f"🔴 Redis connection failed: {e}")
            time.sleep(interval)
    thread = threading.Thread(target=ping_redis, daemon=True)
    thread.start()

monitor_take_profits(interval=10)
keep_alive()
redis_keep_alive()


