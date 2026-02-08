taskkill /f /im python.exe
@echo off
:loop
echo [SYSTEM] Запуск бота...
:: Замени main.py на реальное имя твоего файла
python src/bot.py
echo [SYSTEM] Бот завершил работу. Перезапуск через 3 секунды...
timeout /t 3
goto loop