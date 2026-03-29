@echo off
echo ============================================
echo   POLYBOT SETUP - Installing dependencies
echo ============================================
echo.

C:\Python314\python.exe -m pip install py-clob-client websockets requests python-dotenv flask flask-cors aiohttp

echo.
echo ============================================
echo   SETUP COMPLETE! Starting PolyBot...
echo ============================================
echo.
echo Dashboard will open at: http://localhost:8888
echo.
start "" http://localhost:8888
C:\Python314\python.exe C:\PolyBot\polybot.py
pause
