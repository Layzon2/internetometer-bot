@echo off
REM Запуск бота-интернетометра на Windows
cd /d "%~dp0"

if not exist ".venv" (
    echo [*] Создаю виртуальное окружение...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
echo [*] Устанавливаю зависимости...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

if not exist ".env" (
    echo [!] Файл .env не найден. Создайте его из .env.example и укажите BOT_TOKEN.
    copy .env.example .env >nul
    echo [i] Создан .env — впишите туда токен и запустите снова.
    pause
    exit /b 1
)

echo [*] Запускаю бота...
python bot.py
pause
