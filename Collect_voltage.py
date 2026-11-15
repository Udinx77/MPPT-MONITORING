"""
collect_voltage.py -- MPPT collector (SQLite), jalan di KOMPUTER LOKAL lewat
tunnel SSH SOCKS5:   ssh -D 1080 user@IP

Stdlib only (gak perlu pip install apa-apa): Modbus TCP + handshake SOCKS5
ditulis langsung di sini. Set SOCKS_PROXY = None kalau dijalankan langsung di
jump host. Kalau tunnel SSH belum/lagi mati, siklus di-skip (gak nulis baris
ERROR palsu ke DB) dan lanjut sendiri begitu tunnel hidup lagi.
"""
import os
import socket
import sqlite3
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

# ============================================================
# CONFIGURATION
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DB WAJIB di disk lokal (C:), BUKAN drive jaringan / shared folder / Z: -- di sana SQLite
# bisa rusak ("database disk image is malformed"). server.py pakai path yang sama persis.
DB_FILE = os.path.join(os.environ.get("LOCALAPPDATA") or BASE_DIR, "MPPT-Monitor", "mppt_monitoring.db")

SOCKS_PROXY = ("127.0.0.1", 1080)   
PORT = 502
UNIT_ID = 1
TIMEOUT = 8            # detik; agak longgar karena lewat tunnel SSH
MAX_WORKERS = 20
INTERVAL_SEC = 180     # 3 menit per siklus, 24/7

# WITA = UTC+8 tanpa DST. Offset tetap, jadi gak butuh paket tzdata di Windows.
TZ = timezone(timedelta(hours=8))

# TriStar MPPT register map
REG_VPU_HI = 0x0000
REG_BATTERY = 0x0018
REG_TEMP_HEATSINK = 0x0023
REG_CHARGE_STATE = 0x0032
REG_OUTPUT_POWER = 0x003A

CHARGE_STATES = {
    0: "START",
    1: "NIGHT_CHECK",
    2: "DISCONNECT",
    3: "NIGHT",
    4: "FAULT",
    5: "MPPT",
    6: "ABSORPTION",
    7: "FLOAT",
    8: "EQUALIZE",
    9: "SLAVE",
}

SOCKS_ERRORS = {
    1: "general failure", 2: "ruleset melarang", 3: "network unreachable",
    4: "host unreachable", 5: "connection refused", 6: "TTL expired",
    7: "command not supported", 8: "address type not supported",
}

MPPT_DEVICES = {
    "IP MPPT 1"
}

# ============================================================
# DATABASE
# ============================================================
def ensure_database():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mppt_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device_name TEXT NOT NULL,
                ip TEXT NOT NULL,
                status TEXT NOT NULL,
                battery_voltage REAL,
                array_voltage REAL,
                temp_heatsink REAL,
                temp_rts REAL,
                temp_battery REAL,
                output_power REAL,
                charge_state INTEGER,
                charge_state_name TEXT,
                error TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mppt_logs_device_time
            ON mppt_logs(device_name, timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mppt_logs_device_status_time
            ON mppt_logs(device_name, status, timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mppt_logs_time
            ON mppt_logs(timestamp DESC)
        """)
        conn.commit()


def now_wita():
    return datetime.now(TZ).isoformat(timespec="seconds")


# ============================================================
# NETWORK: SOCKS5 CONNECT + MODBUS TCP (stdlib)
# ============================================================
def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("koneksi putus")
        data += chunk
    return data


def open_connection(ip):
    """TCP ke ip:PORT, lewat SOCKS5 (tunnel SSH) kalau SOCKS_PROXY di-set."""
    if SOCKS_PROXY is None:
        return socket.create_connection((ip, PORT), timeout=TIMEOUT)

    sock = socket.create_connection(SOCKS_PROXY, timeout=TIMEOUT)
    try:
        sock.sendall(b"\x05\x01\x00")                       # SOCKS5, 1 metode: tanpa auth
        if _recv_exact(sock, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS5: proxy menolak metode tanpa auth")
        sock.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(ip) + struct.pack(">H", PORT))
        _, rep, _, atyp = _recv_exact(sock, 4)
        if rep != 0:
            raise ConnectionError(f"SOCKS5: {SOCKS_ERRORS.get(rep, rep)}")
        if atyp == 1:                                          # buang alamat bound dari reply
            _recv_exact(sock, 6)
        elif atyp == 4:
            _recv_exact(sock, 18)
        else:
            _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
        return sock
    except Exception:
        sock.close()
        raise


def read_registers(sock, address, count):
    pdu = struct.pack(">BHH", 0x03, address, count)
    sock.sendall(struct.pack(">HHHB", 1, 0, len(pdu) + 1, UNIT_ID) + pdu)
    _, _, length, _ = struct.unpack(">HHHB", _recv_exact(sock, 7))
    body = _recv_exact(sock, length - 1)
    if body[0] & 0x80:
        raise RuntimeError(f"Modbus exception {body[1]} addr=0x{address:04X}")
    n = body[1]
    values = list(struct.unpack(f">{n // 2}H", body[2:2 + n]))
    if len(values) != count:
        raise RuntimeError(f"Modbus read failed: addr=0x{address:04X}, count={count}")
    return values


def _s16(n):
    return n - 65536 if n > 32767 else n


def fixed_point16(hi, lo):
    return hi + (lo / 65536.0)


# ============================================================
# POLLING
# ============================================================
def poll_device(device_name, ip):
    timestamp = now_wita()
    sock = None

    try:
        sock = open_connection(ip)   # 1 koneksi per device, 5 pembacaan di atasnya

        base = read_registers(sock, REG_VPU_HI, 4)
        v_pu = fixed_point16(base[0], base[1])
        i_pu = fixed_point16(base[2], base[3])

        adc = read_registers(sock, REG_BATTERY, 6)           # 0x0018 - 0x001D
        temps = read_registers(sock, REG_TEMP_HEATSINK, 3)   # 0x0023 - 0x0025
        charge = read_registers(sock, REG_CHARGE_STATE, 1)
        power = read_registers(sock, REG_OUTPUT_POWER, 1)

        charge_state = charge[0]
        return {
            "timestamp": timestamp,
            "device_name": device_name,
            "ip": ip,
            "status": "OK",
            "battery_voltage": round(_s16(adc[0]) * v_pu / 32768.0, 3),
            "array_voltage": round(_s16(adc[3]) * v_pu / 32768.0, 3),
            "temp_heatsink": float(_s16(temps[0])),
            "temp_rts": float(_s16(temps[1])),
            "temp_battery": float(_s16(temps[2])),
            "output_power": round(power[0] * v_pu * i_pu / 131072.0, 2),
            "charge_state": charge_state,
            "charge_state_name": CHARGE_STATES.get(charge_state, f"UNKNOWN ({charge_state})"),
            "error": None,
        }

    except Exception as exc:
        return {
            "timestamp": timestamp,
            "device_name": device_name,
            "ip": ip,
            "status": "ERROR",
            "battery_voltage": None,
            "array_voltage": None,
            "temp_heatsink": None,
            "temp_rts": None,
            "temp_battery": None,
            "output_power": None,
            "charge_state": None,
            "charge_state_name": None,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }

    finally:
        if sock is not None:
            sock.close()


def save_results(results):
    rows = [
        (
            r["timestamp"], r["device_name"], r["ip"], r["status"],
            r["battery_voltage"], r["array_voltage"],
            r["temp_heatsink"], r["temp_rts"], r["temp_battery"],
            r["output_power"], r["charge_state"], r["charge_state_name"],
            r["error"],
        )
        for r in results
    ]

    with sqlite3.connect(DB_FILE, timeout=30) as conn:
        conn.executemany("""
            INSERT INTO mppt_logs (
                timestamp, device_name, ip, status,
                battery_voltage, array_voltage,
                temp_heatsink, temp_rts, temp_battery,
                output_power, charge_state, charge_state_name, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()


def poll_cycle():
    cycle_start = time.time()

    print("\n" + "=" * 80)
    print(f"Polling started: {now_wita()}")
    print(f"Devices: {len(MPPT_DEVICES)}")

    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(poll_device, name, ip): name
            for name, ip in MPPT_DEVICES.items()
        }

        for future in as_completed(futures):
            result = future.result()   # poll_device nangkep semua exception sendiri
            results.append(result)

            if result["status"] == "OK":
                print(
                    f"[OK]    {futures[future]:<35} "
                    f"BAT={result['battery_voltage']}V "
                    f"ARRAY={result['array_voltage']}V "
                    f"POWER={result['output_power']}W"
                )
            else:
                print(f"[ERROR] {futures[future]:<35} {result['error']}")

    save_results(results)

    ok_count = sum(1 for r in results if r["status"] == "OK")
    print("-" * 80)
    print(
        f"Completed: {now_wita()} | OK={ok_count} | "
        f"ERROR={len(results) - ok_count} | Elapsed={time.time() - cycle_start:.1f}s"
    )
    print("=" * 80)


def proxy_up():
    if SOCKS_PROXY is None:
        return True
    try:
        socket.create_connection(SOCKS_PROXY, timeout=2).close()
        return True
    except OSError:
        return False


# ============================================================
# MAIN LOOP
# ============================================================
if __name__ == "__main__":
    try:
        ensure_database()
    except sqlite3.DatabaseError as exc:
        raise SystemExit(
            f"Database bermasalah: {exc}\nFile: {DB_FILE}\n"
            "Hapus file itu (dan file -wal / -shm di sebelahnya kalau ada), lalu jalankan ulang."
        )

    print("MPPT Collector")
    print(f"Database : {DB_FILE}")
    print(f"Devices  : {len(MPPT_DEVICES)}")
    print(f"Interval : {INTERVAL_SEC} seconds")
    print(f"Via      : {'SOCKS5 %s:%s' % SOCKS_PROXY if SOCKS_PROXY else 'koneksi langsung'}")
    print("Press Ctrl+C to stop.")

    waiting = False
    try:
        while True:
            if not proxy_up():
                if not waiting:
                    print(
                        f"[WAIT] Tunnel SSH belum aktif di {SOCKS_PROXY[0]}:{SOCKS_PROXY[1]} "
                        "- login dulu: ssh -D 1080 user@IP (dicek tiap 5 detik)"
                    )
                    waiting = True
                time.sleep(5)
                continue
            waiting = False

            started = time.time()
            try:
                poll_cycle()
            except Exception as exc:
                print(f"[FATAL CYCLE ERROR] {exc}")

            # Interval dihitung dari awal siklus supaya jadwal tetap stabil.
            sleep_for = max(0, INTERVAL_SEC - (time.time() - started))
            print(f"Next polling in {sleep_for:.0f} seconds ({sleep_for / 60:.1f} minutes)...")
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\nCollector stopped.")