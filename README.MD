# Solar MPPT Telemetry & Real-Time Monitoring System

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?style=flat&logo=python&logoColor=white)
![Backend](https://img.shields.io/badge/Backend-ThreadingHTTPServer_(Stdlib)-0284C7?style=flat)
![Database](https://img.shields.io/badge/Database-SQLite3-003B57?style=flat&logo=sqlite&logoColor=white)
![Protocol](https://img.shields.io/badge/Protocol-Modbus_TCP_%7C_SOCKS5-EA580C?style=flat)
![Frontend](https://img.shields.io/badge/Frontend-HTML5_%7C_CSS3_%7C_Vanilla_JS-EAB308?style=flat)

Sistem pemantauan telemetri daya surya (*Solar Charge Controller*) secara *real-time* untuk puluhan unit Morningstar TriStar MPPT yang tersebar pada jaringan operasional lapangan.

Aplikasi ini dirancang menggunakan **100% Python Standard Library** (tanpa dependensi eksternal `pip install`) dengan arsitektur *multi-threaded collector* dan *local HTTP server* yang menggabungkan pembacaan register Modbus TCP melalui SSH SOCKS5 *Tunnel*, penyimpanan historis SQLite lokal, serta antarmuka *Single-Page Application* (SPA) ringan berbasis Vanilla JavaScript dan SVG.

## Fitur Utama

- **Zero-Dependency Pure Python Architecture:** Seluruh komponen *collector* dan *web server* ditulis murni menggunakan pustaka standar Python (`socket`, `struct`, `sqlite3`, `http.server`, `concurrent.futures`), sehingga langsung berjalan di lingkungan Python tanpa instalasi paket tambahan.
- **Native SOCKS5 Proxy & Modbus TCP Framing:** Implementasi *handshake* SOCKS5 dan protokol Modbus TCP *function code* `0x03` secara *native* di level *byte* menggunakan koneksi TCP tunggal per perangkat (5 pembacaan blok register dalam satu sesi) untuk menjangkau jaringan perangkat lapangan yang terisolasi di balik *jump host*.
- **Parallel Polling & Tunnel Auto-Detection:** Menggunakan `ThreadPoolExecutor` (20 *worker* paralel) untuk melakukan *polling* ke seluruh unit setiap 180 detik. Sebelum siklus dimulai, sistem memeriksa ketersediaan port SOCKS5 (`127.0.0.1:1080`); jika *tunnel* SSH terputus, siklus otomatis dijeda tanpa mencatat *false-error* ke database dan langsung pulih saat *tunnel* aktif kembali.
- **Resilient Local SQLite Persistence:** Menyimpan log telemetri terindeks pada disk lokal (`%LOCALAPPDATA%\MPPT-Monitor\mppt_monitoring.db`) guna menghindari *database lock* atau korupsi berkas (`database disk image is malformed`) akibat latensi *network shared drive*.
- **Real-Time Dark Dashboard (SPA):**
  - Pembaruan data dan grafik otomatis setiap 60 detik melalui *asynchronous fetch* tanpa memuat ulang halaman.
  - Pengelompokan prioritas otomatis: **Need Check** (`ERROR`/RTO, tegangan baterai kritis/rendah, atau suhu berlebih) dan **Normal**.
  - Indikator visual status perangkat dengan animasi peringatan (*blinking badge*), estimasi kapasitas baterai (`%SoC`), tegangan baterai (`V`), tegangan *solar array* (`ARR`), serta penghitung durasi *downtime* (`RTO`) sejak pembacaan `OK` terakhir.
- **Analitik Historis & Grafik SVG Interaktif:**
  - Grafik riwayat 24 jam berbasis *inline* SVG yang membandingkan kurva **Battery Voltage** dan **Array Voltage**.
  - Panel inspeksi detail per perangkat yang menampilkan status koneksi, tegangan baterai & panel, suhu *heatsink*/RTS/baterai, daya keluaran (`W`), fase *charging* (`MPPT`, `ABSORPTION`, `FLOAT`, dll.), hingga jejak pesan *error* teknis.

## Arsitektur Sistem

```text
┌──────────────────────────────────┐
│        Jaringan Lapangan         │
│  • TriStar MPPT (Modbus :502)    │
└────────────────┬─────────────────┘
                 │
         [ SSH Jump Host ]
                 ▲
                 ║  SSH Dynamic Port Forwarding (SOCKS5 127.0.0.1:1080)
                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Host Lokal (Windows / Linux)                     │
│                                                                      │
│  ┌────────────────────────────────┐   ┌───────────────────────────┐  │
│  │       collect_voltage.py       │   │         server.py         │  │
│  │  • ThreadPoolExecutor (20)     │   │  • ThreadingHTTPServer    │  │
│  │  • Native SOCKS5 + Modbus TCP  │   │  • Bind 127.0.0.1:8000    │  │
│  │  • Interval Polling: 180s      │   │  • REST API & Static SPA  │  │
│  └───────────────┬────────────────┘   └─────────────▲─────────────┘  │
│                  │                                  │                │
│                  ▼                                  │                │
│     [ SQLite Local DB: %LOCALAPPDATA%\MPPT-Monitor\mppt_monitoring.db ]
└─────────────────────────────────────────────────────┼────────────────┘
                                                      │ HTTP JSON
                                                      ▼
                                     ┌─────────────────────────────────┐
                                     │       Web Dashboard (SPA)       │
                                     │ http://localhost:8000/dashboard.html
                                     └─────────────────────────────────┘
```

## Telemetri Modbus TCP (TriStar MPPT)

Modul kolektor membaca *holding register* pada *controller* Morningstar TriStar MPPT menggunakan satu koneksi TCP per siklus per unit untuk meminimalkan *overhead* di atas *tunnel* SSH:

| Register (Hex) | Parameter | Keterangan & Formula Konversi |
|---|---|---|
| `0x0000 – 0x0003` | `V_PU` & `I_PU` | Faktor skala *fixed-point* 16-bit (`hi + lo / 65536.0`) |
| `0x0018 – 0x001D` | `Battery` & `Array Voltage` | Tegangan baterai & panel surya (`raw_s16 * V_PU / 32768.0`) |
| `0x0023 – 0x0025` | `Temperatures` | Suhu *heatsink*, sensor RTS, dan baterai (`°C`, *signed 16-bit*) |
| `0x0032` | `Charge State` | Fase pengisian (`START`, `NIGHT_CHECK`, `DISCONNECT`, `NIGHT`, `FAULT`, `MPPT`, `ABSORPTION`, `FLOAT`, `EQUALIZE`, `SLAVE`) |
| `0x003A` | `Output Power` | Daya keluaran aktual dalam Watt (`raw * V_PU * I_PU / 131072.0`) |

- **Estimasi State of Charge (SoC):** Persentase baterai (`0% – 100%`) dihitung melalui interpolasi linear dari rentang tegangan sistem aki 24V (`20.0V = 0%` hingga `28.0V = 100%`).

## Struktur Direktori

```text
mppt-monitoring/
├── collect_voltage.py      # Collector paralel Modbus TCP over SOCKS5 -> SQLite
├── server.py               # ThreadingHTTPServer lokal & penyedia endpoint REST API (:8000)
├── dashboard.html          # Antarmuka dashboard SPA (HTML5, CSS3, Vanilla JS, SVG Charts)
├── run.bat                 # Skrip otomatisasi startup Windows (SSH Tunnel + Server + Collector + Browser)
├── .gitignore              # Pengecualian database SQLite, cache Python, dan konfigurasi lokal
└── README.md               # Dokumentasi proyek

# Lokasi Database Runtime (Otomatis dibuat pada disk lokal):
%LOCALAPPDATA%/MPPT-Monitor/mppt_monitoring.db
```

## Instalasi & Cara Menjalankan

### Prasyarat
- **Python 3.8** atau lebih baru (tidak memerlukan pustaka pihak ketiga / `pip install`).
- **OpenSSH Client** (bawaan Windows 10/11 atau Linux/macOS) untuk fitur *SSH SOCKS5 tunneling* ke jaringan MPPT.

### 1. Clone Repositori
```bash
git clone [https://github.com/Udinx77/MPPT-MONITORING.git](https://github.com/Udinx77/MPPT-MONITORING.git)
cd MPPT-MONITORING
```

### 2. Konfigurasi Target SSH, Perangkat & Parameter
Sesuaikan parameter jaringan dan daftar IP perangkat di bagian atas `collect_voltage.py` (serta kredensial *jump host* pada `run.bat` jika menggunakan Windows):

```python
SOCKS_PROXY = ("127.0.0.1", 1080)  # Set None jika dijalankan langsung tanpa SSH tunnel
PORT = 502                         # Port Modbus TCP standar
UNIT_ID = 1                        # Modbus Slave/Unit ID
MAX_WORKERS = 20                   # Jumlah thread polling paralel
INTERVAL_SEC = 180                 # Interval siklus pembacaan MPPT (detik)

MPPT_DEVICES = {
    "UNIT-MPPT-01": "10.x.x.x",
    "UNIT-MPPT-02": "10.x.x.x",
}
```

### 3. Menjalankan Aplikasi

- **Opsi A — Otomatis di Windows (`run.bat`):**
  Klik dua kali `run.bat` atau jalankan melalui Command Prompt. Skrip akan mengecek port `1080` (membuka jendela `ssh -D 1080 user@IP` jika belum aktif), menjalankan `server.py` dan `collect_voltage.py` di jendela terpisah, lalu membuka *dashboard* di Microsoft Edge.

- **Opsi B — Manual (Multi-Terminal / Linux / macOS):**
  - **Terminal 1 (Buka SSH SOCKS5 Tunnel):**
    ```bash
    ssh -N -D 127.0.0.1:1080 user@jump-host-ip
    ```
  - **Terminal 2 (Jalankan Collector MPPT):**
    ```bash
    python collect_voltage.py
    ```
  - **Terminal 3 (Jalankan Web Server Dashboard):**
    ```bash
    python server.py
    ```
  - Buka browser dan akses `http://localhost:8000/dashboard.html`.

## Dokumentasi REST API

Server HTTP lokal mengikat ke `127.0.0.1:8000` agar tetap aman dari akses luar jaringan dan tidak memicu peringatan Windows Firewall:

| Method | Endpoint | Parameter | Deskripsi |
|---|---|---|---|
| `GET` | `/` atau `/dashboard.html` | — | Menyajikan halaman utama *dashboard* SPA. |
| `GET` | `/api/latest` | — | Mengambil catatan telemetri terbaru seluruh perangkat beserta `last_ok` untuk kalkulasi durasi RTO. |
| `GET` | `/api/history` | `device=<NAMA>&hours=24` | Mengambil riwayat tegangan, suhu, daya, dan status pengisian perangkat dalam rentang `1` hingga `168` jam terakhir. |
| `GET` | `/api/health` | — | Memeriksa ketersediaan berkas database SQLite dan *timestamp* data terakhir yang masuk. |

## Logika Klasifikasi Status & Integritas Penyimpanan

- **Tingkat Prioritas Kartu Perangkat:**
  - **Severity 3 — Kritis / Merah (`Need Check`):** Status polling `ERROR` (RTO), tegangan baterai `< 20.0V`, suhu *heatsink* `>= 80°C` (ambang *thermal derating*), atau suhu sensor RTS/baterai `<= 0°C` / `>= 45°C`.
  - **Severity 2 — Peringatan / Kuning (`Need Check`):** Tegangan baterai berada pada rentang `20.0V – 22.0V`.
  - **Severity 0 — Normal:** Perangkat merespons `OK` dengan tegangan baterai `> 22.0V` dan suhu operasional normal (suhu *heatsink* `60°C – 79°C` menampilkan indikator peringatan pada kartu tanpa memindahkannya ke daftar kritis).
- **Perhitungan Durasi RTO:** Apabila perangkat gagal dijangkau (`ERROR`), *dashboard* otomatis menghitung selisih waktu sejak `last_ok` terakhir dalam format menit, jam, atau hari (`RTO: 15m`, `RTO: 2j10m`, `RTO: 1h4j`).
- **Keamanan & Performa Database:** Berkas SQLite selalu ditempatkan pada direktori lokal (`%LOCALAPPDATA%\MPPT-Monitor`) dengan indeks komposit (`device_name`, `status`, `timestamp`) agar kueri API tetap cepat dan terhindar dari kerusakan struktur WAL pada *network drive*.
