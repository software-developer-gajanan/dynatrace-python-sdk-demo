"""
sample_app.py - multi-endpoint demo application for Dynatrace trace testing.

Uses the Dynatrace OneAgent SDK for Python (install: python -m pip install oneagent-sdk).
Every SDK-related block is marked:
    # >>> SDK ADDED ...
    # <<< SDK END

Modes
  server  - web application with many endpoints + a browser UI (default for testing)
  worker  - background loop that runs one processing cycle every few seconds

Endpoints (server mode) - each one shows up as its own endpoint in Dynatrace
  /work              one processing cycle (outgoing call to APP_URL + database work)
  /customers         list customers                       (1 SQL)
  /inventory         list stock                           (1 SQL)
  /inventory/check   check one item                       (1 SQL)  ?item=widget&qty=2
  /orders            list recent orders                   (1 SQL, join)
  /orders/create     create an order                      (SQL read + insert + update)
  /payment/charge    simulated payment                    (custom service)  ?amount=20&fail=1
  /checkout          full flow: calls /inventory/check and /payment/charge over HTTP,
                     then writes the order  -> multi-hop distributed trace   ?fail=1
  /reports/daily     heavier report                       (3 SQL + CPU work)
  /search            search items and customers           (2 SQL)  ?q=wid
  /slow              slow request                         ?ms=2500
  /error             always fails with HTTP 500 (for failure analysis)

Failure module (each one produces a real, different kind of failure in the trace)
  /fail/exception    unhandled exception inside a traced step      -> HTTP 500   (?qty=5 succeeds)
  /fail/db           SQL error (table does not exist)              -> HTTP 500
  /fail/downstream   outgoing call to a service that is down       -> HTTP 502
  /fail/timeout      outgoing call that times out                  -> HTTP 504
  /fail/random       fails randomly                                -> HTTP 500   (?rate=0.5)
  /checkout?fail=1   payment step declined (HTTP 402 from the payment service)
  /health, /, /api/state, /api/routes   not traced (health check, UI, UI data)

Usage:
  venv/bin/python sample_app.py --mode server --port 8085     then open http://<server-ip>:8085/
  venv/bin/python sample_app.py --mode worker

Optional environment variables:
  APP_URL           URL called by /work and the worker (default https://example.com)
  APP_TIMEOUT       seconds to wait for outgoing calls (default 3)
  APP_INTERVAL      seconds between worker cycles (default 10)
  APP_CYCLES        number of worker cycles, 0 = forever (default 0)
  APP_DB            SQLite file (default sample_app.db)
  APP_LOG           log file (default sample_app.log)
  APP_SERVICE_NAME  service name shown in Dynatrace (default SampleApp)
"""

import argparse
import json
import logging
import os
import random
import signal
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# >>> SDK ADDED (Step 1): import the OneAgent SDK -------------------------------
try:
    import oneagent
    from oneagent.common import AgentState, DYNATRACE_HTTP_HEADER_NAME
except ImportError:
    raise SystemExit("oneagent-sdk is not installed. Run: python -m pip install oneagent-sdk")
# <<< SDK END

APP_URL = os.environ.get("APP_URL", "https://example.com")
APP_TIMEOUT = float(os.environ.get("APP_TIMEOUT", "3"))
INTERVAL = int(os.environ.get("APP_INTERVAL", "10"))
CYCLES = int(os.environ.get("APP_CYCLES", "0"))
DB_PATH = os.environ.get("APP_DB", "sample_app.db")
LOG_PATH = os.environ.get("APP_LOG", "sample_app.log")
SERVICE_NAME = os.environ.get("APP_SERVICE_NAME", "SampleApp")
VIRTUAL_HOST = os.environ.get("APP_HOST", socket.gethostname())
SERVER_PORT = 8085  # set from --port in main()

# >>> SDK ADDED (Step 2): reusable SDK objects, created in init_sdk() -----------
sdk = None
SDK_DB_INFO = None
SDK_WEB_INFO = None
# <<< SDK END

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("sample_app")
LAST_CYCLE = {}


# >>> SDK ADDED (Step 2): initialize the SDK --------------------------------------
def init_sdk():
    """Call once at startup, before any tracer is used."""
    global sdk, SDK_DB_INFO, SDK_WEB_INFO
    log.info("OneAgent SDK init result: %r", oneagent.initialize())
    sdk = oneagent.get_sdk()
    names = {getattr(AgentState, n): n for n in dir(AgentState) if n.isupper()}
    state = sdk.agent_state
    log.info("OneAgent agent_state=%s (%s)", state, names.get(state, "?"))
    if state not in (AgentState.ACTIVE, AgentState.TEMPORARILY_INACTIVE):
        log.warning("OneAgent is not active for this process - no traces will be sent")
    SDK_DB_INFO = sdk.create_database_info(
        "SampleAppDB", oneagent.sdk.DatabaseVendor.SQLITE,
        oneagent.sdk.Channel(oneagent.sdk.ChannelType.IN_PROCESS))
    SDK_WEB_INFO = sdk.create_web_application_info(
        virtual_host=VIRTUAL_HOST, application_id=SERVICE_NAME, context_root="/")


def trace_ctx():
    """Optional: trace/span id prefix so Dynatrace links the log line to the trace."""
    try:
        ti = sdk.tracecontext_get_current()
        return "[!dt dt.trace_id=%s,dt.span_id=%s] " % (ti.trace_id, ti.span_id)
    except Exception:
        return ""
# <<< SDK END


# --------------------------------------------------------------------------
# Database (SQLite, so no database server is needed)
# --------------------------------------------------------------------------
CUSTOMERS = [("Asha Patel", "Mumbai"), ("Rohan Mehta", "Pune"), ("Neha Singh", "Delhi"),
             ("Karan Shah", "Mumbai"), ("Priya Nair", "Chennai")]
ITEMS = [("widget", 120, 9.5), ("gadget", 60, 24.0), ("gizmo", 35, 49.9),
         ("doohickey", 80, 15.25), ("sprocket", 200, 3.75), ("flange", 45, 12.0)]


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT, status TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, city TEXT);
            CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER, price REAL);
            CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER, item TEXT, qty INTEGER, amount REAL, status TEXT, created REAL);
        """)
        if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            conn.executemany("INSERT INTO customers (name, city) VALUES (?, ?)", CUSTOMERS)
        if conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0:
            conn.executemany("INSERT INTO inventory (item, stock, price) VALUES (?, ?, ?)", ITEMS)


def db(sql, params=(), fetch="all"):
    """Run ONE SQL statement. fetch: 'all' = rows, 'one' = first row, None = write."""
    # >>> SDK ADDED (Step 3): every SQL statement is traced here, in one place -----
    with sdk.trace_sql_database_request(SDK_DB_INFO, sql) as tracer:
        # <<< SDK END
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(sql, params)
            if fetch == "all":
                rows = cur.fetchall()
                tracer.set_rows_returned(len(rows))          # >>> SDK ADDED (Step 3)
                return rows
            if fetch == "one":
                return cur.fetchone()
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


# --------------------------------------------------------------------------
# Outgoing HTTP helper
# --------------------------------------------------------------------------
def http_get(url, timeout=None):
    req = urllib.request.Request(url, headers={"User-Agent": "sample-app"})
    # >>> SDK ADDED (Step 4): trace the outgoing web request ---------------------
    with sdk.trace_outgoing_web_request(url, "GET", dict(req.header_items())) as tracer:
        tag = tracer.outgoing_dynatrace_string_tag      # lets Dynatrace link to the called service
        if tag:
            if isinstance(tag, bytes):
                tag = tag.decode("ascii")
            req.add_header(DYNATRACE_HTTP_HEADER_NAME, tag)
        # <<< SDK END
        try:
            with urllib.request.urlopen(req, timeout=timeout or APP_TIMEOUT) as resp:
                body = resp.read()
                tracer.set_status_code(resp.status)      # >>> SDK ADDED (Step 4)
                return resp.status, body
        except urllib.error.HTTPError as exc:
            tracer.set_status_code(exc.code)             # >>> SDK ADDED (Step 4)
            raise


def _cpu_work(seconds):
    end, x = time.time() + seconds, 0
    while time.time() < end:
        x += sum(i * i for i in range(1000))
    return x


def _arg(q, name, default):
    return q.get(name, [default])[0]


class AppError(Exception):
    """Error that maps to an HTTP status code (used by the failure module)."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Endpoint handlers - each returns (http_status, dict)
# --------------------------------------------------------------------------
def h_customers(q):
    rows = db("SELECT id, name, city FROM customers ORDER BY id")
    return 200, {"customers": [{"id": r[0], "name": r[1], "city": r[2]} for r in rows]}


def h_inventory(q):
    rows = db("SELECT item, stock, price FROM inventory ORDER BY item")
    return 200, {"inventory": [{"item": r[0], "stock": r[1], "price": r[2]} for r in rows]}


def h_inventory_check(q):
    item, qty = _arg(q, "item", "widget"), int(_arg(q, "qty", "1"))
    row = db("SELECT stock, price FROM inventory WHERE item = ?", (item,), "one")
    if row is None:
        return 404, {"error": "unknown item", "item": item}
    return 200, {"item": item, "qty": qty, "available": row[0] >= qty, "stock": row[0], "price": row[1]}


def h_payment_charge(q):
    amount = float(_arg(q, "amount", "10"))
    # >>> SDK ADDED (Step 5): an internal step shown as its own span ---------------
    with sdk.trace_custom_service("authorize_payment", SERVICE_NAME):
        # <<< SDK END
        time.sleep(random.uniform(0.2, 0.6))                 # simulated bank call
    if _arg(q, "fail", "0") == "1":
        return 402, {"approved": False, "error": "payment declined"}
    return 200, {"approved": True, "amount": amount, "txn": "TXN-%06d" % random.randint(0, 999999)}


def h_orders(q):
    rows = db("SELECT o.id, c.name, o.item, o.qty, o.amount, o.status FROM orders o "
              "JOIN customers c ON c.id = o.customer_id ORDER BY o.id DESC LIMIT 20")
    return 200, {"orders": [{"id": r[0], "customer": r[1], "item": r[2], "qty": r[3],
                             "amount": r[4], "status": r[5]} for r in rows]}


def _place_order(item, qty, amount, status="paid"):
    customer_id = random.choice(db("SELECT id FROM customers"))[0]
    order_id = db("INSERT INTO orders (customer_id, item, qty, amount, status, created) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (customer_id, item, qty, amount, status, time.time()), None)
    db("UPDATE inventory SET stock = MAX(stock - ?, 0) WHERE item = ?", (qty, item), None)
    return order_id


def h_orders_create(q):
    item, qty = _arg(q, "item", random.choice(ITEMS)[0]), int(_arg(q, "qty", str(random.randint(1, 4))))
    row = db("SELECT price FROM inventory WHERE item = ?", (item,), "one")
    if row is None:
        return 404, {"error": "unknown item", "item": item}
    order_id = _place_order(item, qty, round(row[0] * qty, 2), "created")
    return 201, {"order_id": order_id, "item": item, "qty": qty}


def h_checkout(q):
    item, qty = _arg(q, "item", random.choice(ITEMS)[0]), int(_arg(q, "qty", str(random.randint(1, 3))))
    base = "http://127.0.0.1:%d" % SERVER_PORT
    # >>> SDK ADDED (Step 5): the whole checkout is one traced service step ---------
    with sdk.trace_custom_service("checkout_flow", SERVICE_NAME):
        # <<< SDK END
        try:
            # these two calls go to THIS app over HTTP -> separate requests joined in one trace
            _, body = http_get("%s/inventory/check?item=%s&qty=%d" % (base, urllib.parse.quote(item), qty), 10)
            stock = json.loads(body)
            if not stock["available"]:
                return 409, {"error": "out of stock", "item": item}
            amount = round(stock["price"] * qty, 2)
            fail = "&fail=1" if _arg(q, "fail", "0") == "1" else ""
            http_get("%s/payment/charge?amount=%.2f%s" % (base, amount, fail), 10)
        except urllib.error.HTTPError as exc:
            return exc.code, {"error": "checkout step failed", "step_status": exc.code}
        order_id = _place_order(item, qty, amount)
    sdk.add_custom_request_attribute("order_amount", amount)     # >>> SDK ADDED (Step 5)
    return 200, {"order_id": order_id, "item": item, "qty": qty, "amount": amount}


def h_reports_daily(q):
    # >>> SDK ADDED (Step 5) ---------------------------------------------------------
    with sdk.trace_custom_service("build_daily_report", SERVICE_NAME):
        # <<< SDK END
        totals = db("SELECT status, COUNT(*), COALESCE(SUM(amount), 0) FROM orders GROUP BY status")
        top = db("SELECT item, SUM(qty) FROM orders GROUP BY item ORDER BY 2 DESC LIMIT 3")
        cities = db("SELECT c.city, COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id "
                    "GROUP BY c.city")
        _cpu_work(0.3)                                      # simulated aggregation work
    return 200, {"by_status": [list(r) for r in totals], "top_items": [list(r) for r in top],
                 "by_city": [list(r) for r in cities]}


def h_search(q):
    term = _arg(q, "q", "")
    items = db("SELECT item, price FROM inventory WHERE item LIKE ?", ("%" + term + "%",))
    people = db("SELECT name, city FROM customers WHERE name LIKE ? OR city LIKE ?",
                ("%" + term + "%", "%" + term + "%"))
    return 200, {"q": term, "items": [list(r) for r in items], "customers": [list(r) for r in people]}


def h_slow(q):
    delay = min(float(_arg(q, "ms", "2500")), 10000) / 1000.0
    with sdk.trace_custom_service("slow_operation", SERVICE_NAME):   # >>> SDK ADDED (Step 5)
        time.sleep(delay)
    return 200, {"slept_seconds": delay}


def h_error(q):
    raise RuntimeError("Simulated failure for testing")


# --------------------------------------------------------------------------
# FAILURE MODULE - endpoints that fail on purpose so failures can be analysed in Dynatrace
# --------------------------------------------------------------------------
def f_exception(q):
    # >>> SDK ADDED (Failure module): an exception that leaves a traced step marks that
    # step as failed and the exception text is attached to the trace (re-raised after).
    with sdk.trace_custom_service("validate_order", SERVICE_NAME):
        qty = int(_arg(q, "qty", "abc"))        # ValueError unless ?qty=<number> is given
    # <<< SDK END
    return 200, {"qty": qty}


def f_db(q):
    # SQL error: the SQL span is marked failed by the SDK because the exception passes through it
    db("SELECT * FROM orders_archive WHERE id = ?", (1,))      # table does not exist
    return 200, {}


def f_downstream(q):
    try:
        http_get("http://127.0.0.1:9/unavailable", 2)           # nothing listens on port 9
    except Exception as exc:
        raise AppError(502, "downstream service unavailable: %s" % exc)
    return 200, {}


def f_timeout(q):
    try:
        # /slow needs 4 s, we only wait 1 s -> timeout. The slow call still shows in the trace.
        http_get("http://127.0.0.1:%d/slow?ms=4000" % SERVER_PORT, 1)
    except Exception as exc:
        raise AppError(504, "downstream timeout: %s" % exc)
    return 200, {}


def f_random(q):
    rate = float(_arg(q, "rate", "0.5"))
    if random.random() < rate:
        raise AppError(500, "random failure (rate=%s)" % rate)
    return 200, {"result": "ok"}


def store_records(count):
    for i in range(count):
        db("INSERT INTO jobs (payload, status, created) VALUES (?, ?, ?)",
           ("record-%d-%d" % (int(time.time()), i), "new", time.time()), None)
    log.info("Inserted %d records", count)


def process_records():
    pending = db("SELECT COUNT(*) FROM jobs WHERE status = 'new'", (), "one")[0]
    time.sleep(random.uniform(0.2, 1.0))
    done = db("UPDATE jobs SET status = 'done' WHERE status = 'new'", (), None)
    log.info("Processed %d of %d pending records", done, pending)
    return done


def fetch_remote_data():
    try:
        status, body = http_get(APP_URL)
        log.info("Fetched %s status=%s bytes=%d", APP_URL, status, len(body))
        return status
    except Exception as exc:
        log.warning("Outgoing request to %s failed: %s", APP_URL, exc)
        return None


def run_cycle():
    global LAST_CYCLE
    # >>> SDK ADDED (Step 5): the whole cycle is one traced service step -------------
    with sdk.trace_custom_service("run_cycle", SERVICE_NAME):
        # <<< SDK END
        started = time.time()
        status = fetch_remote_data()
        store_records(random.randint(3, 8))
        done = process_records()
        removed = db("DELETE FROM jobs WHERE id NOT IN (SELECT id FROM jobs ORDER BY id DESC LIMIT ?)",
                     (200,), None)
        elapsed = time.time() - started
        sdk.add_custom_request_attribute("records_processed", done)   # >>> SDK ADDED (Step 5)
        log.info("%sCycle finished in %.2fs (http=%s, processed=%d, cleaned=%d)",
                 trace_ctx(), elapsed, status, done, removed)
        LAST_CYCLE = {"at": time.strftime("%H:%M:%S"), "seconds": round(elapsed, 2),
                      "http_status": status, "processed": done}
        return done


def h_work(q):
    return 200, {"processed": run_cycle()}


ROUTES = {
    "/work": h_work, "/customers": h_customers, "/inventory": h_inventory,
    "/inventory/check": h_inventory_check, "/payment/charge": h_payment_charge,
    "/orders": h_orders, "/orders/create": h_orders_create, "/checkout": h_checkout,
    "/reports/daily": h_reports_daily, "/search": h_search, "/slow": h_slow, "/error": h_error,
    "/fail/exception": f_exception, "/fail/db": f_db, "/fail/downstream": f_downstream,
    "/fail/timeout": f_timeout, "/fail/random": f_random,
}
# (button label, URL used by the UI)
UI_ROUTES = [
    ("/work", "/work"), ("/customers", "/customers"), ("/inventory", "/inventory"),
    ("/inventory/check", "/inventory/check?item=widget&qty=2"), ("/orders", "/orders"),
    ("/orders/create", "/orders/create"), ("/payment/charge", "/payment/charge?amount=20"),
    ("/checkout", "/checkout"),
    ("/reports/daily", "/reports/daily"), ("/search", "/search?q=wid"),
    ("/slow", "/slow?ms=2500"),
]
FAIL_UI_ROUTES = [
    ("/fail/exception", "/fail/exception"), ("/fail/db", "/fail/db"),
    ("/fail/downstream", "/fail/downstream"), ("/fail/timeout", "/fail/timeout"),
    ("/fail/random", "/fail/random"), ("/error", "/error"),
    ("/checkout (payment fails)", "/checkout?fail=1"),
]


def get_state():
    def one(sql):
        return db(sql, (), "one")[0]
    orders = db("SELECT o.id, c.name, o.item, o.qty, o.amount, o.status FROM orders o "
                "JOIN customers c ON c.id = o.customer_id ORDER BY o.id DESC LIMIT 8")
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            tail = [l.rstrip("\n") for l in f.readlines()[-14:]]
    except OSError:
        tail = []
    return {"customers": one("SELECT COUNT(*) FROM customers"), "orders": one("SELECT COUNT(*) FROM orders"),
            "stock": one("SELECT COALESCE(SUM(stock), 0) FROM inventory"),
            "revenue": round(one("SELECT COALESCE(SUM(amount), 0) FROM orders"), 2),
            "recent_orders": [list(r) for r in orders], "log": tail, "last_cycle": LAST_CYCLE}


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Sample App</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f6fa;color:#222}
 header{background:#1f3a5f;color:#fff;padding:14px 22px;font-size:20px}
 main{max-width:1000px;margin:18px auto;padding:0 16px}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}
 .card{background:#fff;border:1px solid #dde3ec;border-radius:6px;padding:10px 16px;min-width:130px}
 .card b{display:block;font-size:21px;color:#1f3a5f}
 .btns{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
 button{background:#1f3a5f;color:#fff;border:0;border-radius:4px;padding:8px 14px;font-size:13px;cursor:pointer}
 button.alt{background:#5a6b82} button.stop{background:#b3261e}
 table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #dde3ec}
 th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #eef1f6;font-size:13px}
 th{background:#e4eaf2}
 pre{background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:6px;font-size:12px;overflow:auto;max-height:230px;white-space:pre-wrap}
 h3{margin:16px 0 6px;color:#1f3a5f} #status{color:#555;margin-left:10px;font-size:13px}
</style></head><body>
<header>Sample App - Dynatrace multi-endpoint test</header>
<main>
 <div class="cards">
  <div class="card">Customers<b id="s_customers">-</b></div>
  <div class="card">Orders<b id="s_orders">-</b></div>
  <div class="card">Items in stock<b id="s_stock">-</b></div>
  <div class="card">Revenue<b id="s_revenue">-</b></div>
 </div>
 <h3>Endpoints <span id="status"></span></h3>
 <div class="btns" id="btns"></div>
 <h3>Failure scenarios</h3>
 <div class="btns" id="btns_fail"></div>
 <div class="btns"><button class="alt" id="auto" onclick="toggleAuto()">Start auto traffic</button>
  <span style="font-size:12px;color:#555;align-self:center">calls a random endpoint every 1.5 s (about 1 in 5 is a failure)</span></div>
 <h3>Last response</h3><pre id="resp">Click an endpoint...</pre>
 <h3>Recent orders</h3>
 <table><thead><tr><th>ID</th><th>Customer</th><th>Item</th><th>Qty</th><th>Amount</th><th>Status</th></tr></thead>
 <tbody id="orders"></tbody></table>
 <h3>Live log</h3><pre id="log"></pre>
</main>
<script>
const ROUTES = __ROUTES__;
let timer = null;
function el(id){return document.getElementById(id)}
async function call(url){
  const t0 = performance.now();
  try{
    const r = await fetch(url); const txt = await r.text();
    el('resp').textContent = 'GET ' + url + '  ->  ' + r.status + '  (' + Math.round(performance.now()-t0) + ' ms)\\n' + txt;
    el('status').textContent = url + ' -> ' + r.status;
  }catch(e){ el('resp').textContent = 'Request failed: ' + e; }
  refresh();
}
function buildButtons(){
  ROUTES.forEach(([label,url,kind]) => { const b=document.createElement('button');
    if(kind==='fail') b.className='stop';
    b.textContent=label; b.onclick=()=>call(url);
    el(kind==='fail'?'btns_fail':'btns').appendChild(b); });
}
async function refresh(){
  try{
    const s = await (await fetch('/api/state')).json();
    el('s_customers').textContent=s.customers; el('s_orders').textContent=s.orders;
    el('s_stock').textContent=s.stock; el('s_revenue').textContent=s.revenue;
    const tb=el('orders'); tb.innerHTML='';
    s.recent_orders.forEach(o=>{const tr=document.createElement('tr');
      o.forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.appendChild(td)}); tb.appendChild(tr)});
    const lg=el('log'); lg.textContent=s.log.join('\\n'); lg.scrollTop=lg.scrollHeight;
  }catch(e){}
}
function toggleAuto(){
  const b=el('auto');
  if(timer){clearInterval(timer);timer=null;b.textContent='Start auto traffic';b.className='alt';return}
  timer=setInterval(()=>{
    const ok=ROUTES.filter(r=>r[2]!=='fail'), bad=ROUTES.filter(r=>r[2]==='fail');
    const pool=(Math.random()<0.8)?ok:bad;            // about 1 call in 5 is a failure
    call(pool[Math.floor(Math.random()*pool.length)][1]) },1500);
  b.textContent='Stop auto traffic'; b.className='stop';
}
buildButtons(); refresh(); setInterval(refresh,5000);
</script></body></html>""".replace("__ROUTES__", json.dumps([[l, u, "ok"] for l, u in UI_ROUTES] +
                                  [[l, u, "fail"] for l, u in FAIL_UI_ROUTES]))


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, text):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, q = parsed.path, urllib.parse.parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE)
        elif path == "/api/state":
            self._send(200, "application/json", json.dumps(get_state()))
        elif path == "/api/routes":
            self._send(200, "application/json", json.dumps(sorted(ROUTES)))
        elif path == "/health":
            self._send(200, "text/plain", "ok\n")
        elif path in ROUTES:
            self.traced(path, q)
        else:
            self._send(404, "application/json", json.dumps({"error": "not found", "path": path}))

    def traced(self, path, q):
        # >>> SDK ADDED (Step 6): every business endpoint is one traced incoming web request.
        # Health check, UI page and UI data calls are intentionally NOT traced.
        wreq = sdk.trace_incoming_web_request(
            SDK_WEB_INFO,
            "http://%s%s" % (self.headers.get("Host", "localhost"), self.path),
            "GET",
            headers=dict(self.headers.items()),         # carries the Dynatrace tag from the caller
            remote_address="%s:%s" % self.client_address[:2])
        with wreq:
            # <<< SDK END
            try:
                status, payload = ROUTES[path](q)
            except Exception as exc:
                status = getattr(exc, "status", 500)               # AppError carries its own status
                if isinstance(exc, AppError):
                    log.warning("%s failed: %s", path, exc)
                else:
                    log.exception("Request %s failed", path)
                wreq.mark_failed(type(exc).__name__, str(exc))     # >>> SDK ADDED (Step 6)
                payload = {"error": str(exc), "type": type(exc).__name__}
            wreq.set_status_code(status)                           # >>> SDK ADDED (Step 6)
            log.info("%sGET %s -> %d", trace_ctx(), path, status)
            self._send(status, "application/json", json.dumps(payload))

    def log_message(self, fmt, *args):
        pass  # request lines are logged in traced(); keeps the log readable


def run_server(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("Server listening on port %d - open http://<server-ip>:%d/", port, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log.info("Server stopped")


def run_worker():
    log.info("Worker started (interval=%ss, cycles=%s, url=%s)", INTERVAL, CYCLES or "infinite", APP_URL)
    n = 0
    while True:
        n += 1
        try:
            run_cycle()
        except Exception:
            log.exception("Cycle %d failed", n)
        if CYCLES and n >= CYCLES:
            break
        time.sleep(INTERVAL)
    log.info("Worker stopped")


def main():
    global SERVER_PORT
    parser = argparse.ArgumentParser(description="Multi-endpoint demo app for Dynatrace")
    parser.add_argument("--mode", choices=["worker", "server"], default="server")
    parser.add_argument("--port", type=int, default=8085)
    args = parser.parse_args()
    SERVER_PORT = args.port

    init_sdk()                                  # >>> SDK ADDED (Step 2)
    init_db()

    # >>> SDK ADDED (Step 7): clean stop on "kill"/systemd so the SDK shuts down properly
    def _stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    try:
        # <<< SDK END
        if args.mode == "server":
            run_server(args.port)
        else:
            run_worker()
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        # >>> SDK ADDED (Step 7): release the infos, then shut the SDK down
        SDK_DB_INFO.close()
        SDK_WEB_INFO.close()
        oneagent.shutdown()
        # <<< SDK END


if __name__ == "__main__":
    main()
