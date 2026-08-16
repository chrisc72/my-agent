@echo off
cd /d "%~dp0"

if exist ".env" (
    for /f "usebackq tokens=1,2 delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" (
            set "%%A=%%B"
        )
    )
)

echo Stopping any existing Streamlit processes...
taskkill /f /im streamlit.exe >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq streamlit*" >nul 2>&1

echo Starting PIF Tool...
echo Browser will open automatically. Please wait...
echo.
streamlit run app.py --server.headless false

if errorlevel 1 (
    echo.
    echo Failed to start. Please run: pip install streamlit
    pause
)
