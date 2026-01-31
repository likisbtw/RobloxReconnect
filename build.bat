@echo off
echo Installing dependencies...
pip install -r requirements.txt
echo Building EXE...
pyinstaller --noconsole --onefile --collect-all customtkinter --name "RobloxReconnector" main.py
echo Build Complete! Check the 'dist' folder.
pause
