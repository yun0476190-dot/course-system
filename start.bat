@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo [1/4] 建立虛擬環境...
    python -m venv venv
)

call venv\Scripts\activate

echo [2/4] 安裝套件...
python -m pip install -q -r requirements.txt

echo [3/4] 建立資料庫並灌入假資料...
python manage.py migrate -v 0
python manage.py seed_data

echo [4/4] 啟動伺服器，瀏覽器將自動開啟...
echo       使用期間請不要關閉這個視窗，關掉網站就會停止。
rem 等伺服器可以連線後才開瀏覽器，避免一打開就顯示「無法連上這個網站」
start "" /b python -c "import socket, time, webbrowser; [time.sleep(0.5) for _ in iter(lambda: socket.socket().connect_ex(('127.0.0.1', 8000)), 0)]; webbrowser.open('http://127.0.0.1:8000/')"
python manage.py runserver 127.0.0.1:8000 --noreload

pause