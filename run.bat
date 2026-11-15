@echo off
rem MPPT Monitor - satu klik: tunnel SSH + server + collector + dashboard
cd /d "%~dp0"
title MPPT Monitor Launcher

rem pakai "python", kalau gak ada pakai launcher "py"
where python >nul 2>nul && (set PY=python) || (set PY=py)

rem 1) Tunnel SSH SOCKS5 di 127.0.0.1:1080 - dilewati kalau port 1080 sudah hidup
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient(127.0.0.1,1080)).Close();exit 0}catch{exit 1}"
if errorlevel 1 (
    echo Login SSH di jendela "SSH Tunnel", biarkan jendela itu tetap terbuka.
    start "SSH Tunnel" ssh -D 1080 user@IP
) else (
    echo Tunnel SSH 1080 sudah aktif, dilewati.
)

rem 2) Server dashboard + collector (cmd /k = jendela tetap terbuka kalau error)
start "MPPT Server" cmd /k %PY% server.py
start "MPPT Collector" cmd /k %PY% collect_voltage.py

rem 3) Buka dashboard di Edge
timeout /t 3 /nobreak >nul
start msedge http://localhost:8000/dashboard.html