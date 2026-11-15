import json
import os
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = "127.0.0.1"   # lokal doang (gak narik popup firewall Windows, DB gak kebuka ke LAN)
PORT = 8000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # dashboard.html satu folder dengan file ini
# harus sama persis dengan collect_voltage.py (disk lokal, bukan drive jaringan)
DB_FILE = os.path.join(os.environ.get("LOCALAPPDATA") or BASE_DIR, "MPPT-Monitor", "mppt_monitoring.db")


def query_all(sql, params=()):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


class MPPTHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.path = "/dashboard.html"

        if parsed.path == "/api/latest":
            self.api_latest()
            return

        if parsed.path == "/api/history":
            self.api_history(parse_qs(parsed.query))
            return

        if parsed.path == "/api/health":
            self.api_health()
            return

        super().do_GET()

    def api_latest(self):
        try:
            rows = query_all("""
                SELECT m.*,
                       (SELECT MAX(o.timestamp) FROM mppt_logs o
                         WHERE o.device_name = m.device_name
                           AND o.status = 'OK') AS last_ok
                FROM mppt_logs m
                INNER JOIN (
                    SELECT device_name, MAX(id) AS max_id
                    FROM mppt_logs
                    GROUP BY device_name
                ) x ON x.max_id = m.id
                ORDER BY m.device_name COLLATE NOCASE
            """)

            self.send_json({
                "success": True,
                "count": len(rows),
                "data": rows,
            })

        except Exception as exc:
            self.send_json({
                "success": False,
                "error": str(exc),
                "data": [],
            }, 500)

    def api_history(self, query):
        device = query.get("device", [None])[0]
        hours_raw = query.get("hours", ["24"])[0]

        if not device:
            self.send_json({
                "success": False,
                "error": "Parameter device wajib diisi.",
                "data": [],
            }, 400)
            return

        try:
            hours = max(1, min(int(hours_raw), 168))
        except ValueError:
            hours = 24

        try:
            rows = query_all("""
                SELECT
                    timestamp,
                    device_name,
                    battery_voltage,
                    array_voltage,
                    temp_heatsink,
                    temp_rts,
                    temp_battery,
                    output_power,
                    charge_state,
                    charge_state_name,
                    status
                FROM mppt_logs
                WHERE device_name = ?
                  AND julianday(timestamp) >= julianday('now', ?)
                ORDER BY timestamp ASC
            """, (device, f"-{hours} hours"))

            self.send_json({
                "success": True,
                "device": device,
                "hours": hours,
                "data": rows,
            })

        except Exception as exc:
            self.send_json({
                "success": False,
                "error": str(exc),
                "data": [],
            }, 500)

    def api_health(self):
        try:
            latest = query_one("""
                SELECT timestamp
                FROM mppt_logs
                ORDER BY id DESC
                LIMIT 1
            """)

            self.send_json({
                "success": True,
                "database": os.path.exists(DB_FILE),
                "latest_timestamp": latest["timestamp"] if latest else None,
            })

        except Exception as exc:
            self.send_json({
                "success": False,
                "error": str(exc),
            }, 500)

    def log_message(self, format, *args):
        # Log hanya request API dan halaman HTML.
        print(f"[HTTP] {self.address_string()} - {format % args}")


if __name__ == "__main__":
    if not os.path.exists(os.path.join(BASE_DIR, "dashboard.html")):
        raise SystemExit(f"dashboard.html gak ada di folder: {BASE_DIR}")

    if not os.path.exists(DB_FILE):
        print(f"WARNING: database belum ada: {DB_FILE}")
        print("Jalankan collect_voltage.py terlebih dahulu.")

    server = ThreadingHTTPServer((HOST, PORT), MPPTHandler)

    print("=" * 70)
    print("MPPT Dashboard Server")
    print(f"Dashboard : http://localhost:{PORT}/dashboard.html")
    print(f"API       : http://localhost:{PORT}/api/latest")
    print(f"History   : http://localhost:{PORT}/api/history?device=...&hours=24")
    print(f"DB        : {DB_FILE}")
    print("Press Ctrl+C to stop.")
    print("=" * 70)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()