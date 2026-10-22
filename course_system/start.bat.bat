@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo [1/4] 建立虛擬環境...
    python -m venv venv
)

call venv\Scripts\activate

echo [2/4] 安裝套件...
python -m pip install -q "Django>=5.0,<6.0"

echo [3/4] 建立資料庫並灌入假資料...
python manage.py migrate -v 0
python manage.py seed_data

echo [4/4] 啟動伺服器，瀏覽器將自動開啟...
start "" http://127.0.0.1:8000/
python manage.py runserver 127.0.0.1:8000 --noreload

pause