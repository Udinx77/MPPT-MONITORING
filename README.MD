# Solar MPPT Telemetry & Real-Time Monitoring Dashboard

Sistem pemantauan telemetri *solar charge controller* (Morningstar TriStar MPPT) secara *real-time* yang dibangun menggunakan **100% Python Standard Library** (tanpa dependensi pihak ketiga) dan antarmuka web ringan berbasis Vanilla JavaScript. Dirancang untuk memantau puluhan unit MPPT di jaringan internal terisolasi melalui *tunnel* SSH SOCKS5.

## Fitur Utama

* **Zero-Dependency Architecture:** Seluruh *collector* dan *web server* hanya menggunakan pustaka standar Python (`socket`, `struct`, `sqlite3`, `http.server`, `concurrent.futures`), sehingga langsung berjalan tanpa perlu `pip install`.
* **Built-in SOCKS5 & Modbus TCP Client:** Implementasi *handshake* SOCKS5 dan pembacaan register Modbus TCP (Port `502`, Function Code `0x03`) secara *native* untuk menembus *jump host* SSH.
* **Tunnel-Aware Polling:** Mendeteksi status *tunnel* SSH sebelum menjalankan siklus *polling* 3 menitan (20 *worker* paralel) sehingga tidak mencemari database dengan log error palsu saat koneksi SSH terputus.
* **Resilient Local SQLite Storage:** Menyimpan log telemetri terindeks di direktori `%LOCALAPPDATA%` lokal untuk mencegah korupsi database (*database disk image is malformed*) akibat latensi *network drive*.
* **Prioritized Dark Dashboard & SVG Charts:**
  * Pengelompokan otomatis **Need Check** vs **Normal** berdasarkan ambang tegangan baterai (`< 20V` kritis, `20–22V` peringatan) dan anomali suhu (*Heatsink*, *RTS*, *Battery*).
  * Estimasi persentase *State of Charge* (`%SoC`) baterai 24V dan perhitungan durasi *downtime* (`RTO`) sejak pembacaan `OK` terakhir.
  * Grafik riwayat 24 jam interaktif berbasis *inline* SVG (*Battery Voltage* vs *Array Voltage*).
* **One-Click Windows Launcher:** Skrip `run.bat` otomatis memeriksa port SOCKS5 (`1080`), membuka sesi SSH *tunnel* jika belum aktif, menjalankan *server* serta *collector*, dan membuka *dashboard* di browser.

## Arsitektur Sistem

```text
[TriStar MPPT Units] ──(Modbus TCP :502)──► [Jump Host]
                                                ▲
                                       (SSH SOCKS5 :1080)
                                                │
[Dashboard.html] ◄──(HTTP/JSON :8000)── [Server.py] ◄──► [SQLite DB] ◄── [Collect_voltage.py]