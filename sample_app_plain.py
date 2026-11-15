"""
sample_app.py - simple Python application for Dynatrace trace testing.

Version 1: plain application, NO OneAgent SDK.
Standard library only, so nothing needs to be installed.

Two modes:
  worker  - runs a processing cycle every few seconds
            (outgoing web call + database work + cleanup)
  server  - small HTTP server (GET /work, GET /health)

Usage (PowerShell / Command Prompt):
  py sample_app.py --mode worker
  py sample_app.py --mode server --port 8085

Optional environment variables:
  APP_URL       URL called by the outgoing web request (default https://example.com)
  APP_INTERVAL  seconds between worker cycles (default 10)
  APP_CYCLES    number of cycles, 0 = run forever (default 0)
  APP_DB        SQLite file path (default sample_app.db)
  APP_LOG       log file path (default sample_app.log)
"""

import argparse
import logging
import os
import random
import sqlite3
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

APP_URL = os.environ.get("APP_URL", "https://example.com")
INTERVAL = int(os.environ.get("APP_INTERVAL", "10"))
CYCLES = int(os.environ.get("APP_CYCLES", "0"))
DB_PATH = os.environ.get("APP_DB", "sample_app.db")
LOG_PATH = os.environ.get("APP_LOG", "sample_app.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("sample_app")


# --------------------------------------------------------------------------
# Database helpers (SQLite, so no database server is needed)
# --------------------------------------------------------------------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "payload TEXT, status TEXT, created REAL)"
        )


def db_insert(payload):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO jobs (payload, status, created) VALUES (?, ?, ?)",
            (payload, "new", time.time()),
        )


def db_count_new():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new'").fetchone()[0]


def db_mark_done():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("UPDATE jobs SET status = 'done' WHERE status = 'new'")
        return cur.rowcount


def db_cleanup(keep=200):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "DELETE FROM jobs WHERE id NOT IN "
            "(SELECT id FROM jobs ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        return cur.rowcount


# --------------------------------------------------------------------------
# Business steps - each one is a natural place to add a tracer later
# --------------------------------------------------------------------------
def fetch_remote_data():
    """Outgoing web request."""
    try:
        req = urllib.request.Request(APP_URL, headers={"User-Agent": "sample-app"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            log.info("Fetched %s status=%s bytes=%d", APP_URL, resp.status, len(body))
            return resp.status, len(body)
    except Exception as exc:  # network may be blocked on the server
        log.warning("Outgoing request to %s failed: %s", APP_URL, exc)
        return None, 0


def store_records(count):
    """Database writes."""
    for i in range(count):
        db_insert("record-%d-%d" % (int(time.time()), i))
    log.info("Inserted %d records", count)


def process_records():
    """Database read/update plus simulated CPU work."""
    pending = db_count_new()
    time.sleep(random.uniform(0.2, 1.0))  # simulated processing time
    done = db_mark_done()
    log.info("Processed %d of %d pending records", done, pending)
    return done


def cleanup():
    removed = db_cleanup()
    log.info("Cleanup removed %d old records", removed)
    return removed


# --------------------------------------------------------------------------
# One full cycle (main unit of work)
# --------------------------------------------------------------------------
def run_cycle():
    started = time.time()
    status, size = fetch_remote_data()
    store_records(random.randint(3, 8))
    done = process_records()
    cleanup()
    elapsed = time.time() - started
    log.info("Cycle finished in %.2fs (http=%s, processed=%d)", elapsed, status, done)
    return done


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


# --------------------------------------------------------------------------
# Optional HTTP server mode (incoming web requests)
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, text):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/work"):
            try:
                done = run_cycle()
                self._send(200, "work done, processed=%d\n" % done)
            except Exception as exc:
                log.exception("Request failed")
                self._send(500, "error: %s\n" % exc)
        elif self.path.startswith("/health"):
            self._send(200, "ok\n")
        else:
            self._send(404, "not found\n")

    def log_message(self, fmt, *args):
        log.info("HTTP %s - %s", self.address_string(), fmt % args)


def run_server(port):
    server = HTTPServer(("0.0.0.0", port), Handler)
    log.info("Server listening on port %d (GET /work, GET /health)", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log.info("Server stopped")


def main():
    parser = argparse.ArgumentParser(description="Sample app for Dynatrace trace testing")
    parser.add_argument("--mode", choices=["worker", "server"], default="worker")
    parser.add_argument("--port", type=int, default=8085)
    args = parser.parse_args()

    init_db()
    if args.mode == "server":
        run_server(args.port)
    else:
        run_worker()


if __name__ == "__main__":
    main()
