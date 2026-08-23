@echo off
title Brite Spark 2026 - No Wrong Door

echo.
echo ============================================================
echo   Brite Spark 2026 - Problem 3: No Wrong Door
echo ============================================================
echo.
echo Checking Python installation...
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo ERROR: Python is not installed or not on PATH.
    echo.
    echo Install Python from: https://www.python.org/downloads/
    echo IMPORTANT: Tick "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)
python --version
echo Python OK.
echo.

echo Verifying project files...
python -c "import os, sys; files=['app/api.py','app/assembly.py','data pack/services/rest_service.py','data pack/services/xml_service.py']; missing=[f for f in files if not os.path.exists(f)]; [print('MISSING:', f) for f in missing]; sys.exit(1) if missing else print('All files OK.')"
IF ERRORLEVEL 1 (
    echo.
    echo ERROR: One or more required files are missing.
    echo Make sure you are running this from the repo root folder.
    echo.
    pause
    exit /b 1
)
echo.

echo Starting services in separate windows...
echo.

echo [1/3] Starting REST Mock Service on port 8081...
start "REST Mock - Port 8081" cmd /k "python "data pack/services/rest_service.py" --port 8081"

timeout /t 2 /nobreak >nul

echo [2/3] Starting XML Mock Service on port 8082 (failure-rate 0.40)...
start "XML Mock - Port 8082" cmd /k "python "data pack/services/xml_service.py" --port 8082 --failure-rate 0.40"

timeout /t 3 /nobreak >nul

echo [3/3] Starting Unified API on port 8090...
start "Unified API - Port 8090" cmd /k "python -m app.api --port 8090"

timeout /t 3 /nobreak >nul

echo.
echo ============================================================
echo   All 3 services started in separate windows.
echo ============================================================
echo.
echo   REST Mock      ->  http://127.0.0.1:8081
echo   XML Mock       ->  http://127.0.0.1:8082
echo   Unified API    ->  http://127.0.0.1:8090
echo.
echo ============================================================
echo   Health Check:
echo ============================================================
echo.
python -c "import urllib.request, json, time; time.sleep(1); r=urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=5).read(); print(json.dumps(json.loads(r), indent=2))" 2>nul
IF ERRORLEVEL 1 (
    echo   Health check failed. The API may still be starting up.
    echo   Wait a few seconds and open: http://127.0.0.1:8090/health
)
echo.
echo ============================================================
echo   Quick Test URLs:
echo ============================================================
echo.
echo   All Residents:
echo   http://127.0.0.1:8090/unified/residents
echo.
echo   Single Resident (by REST ID):
echo   http://127.0.0.1:8090/unified/residents/R-10697
echo.
echo   Same Resident (by XML Reference - No Wrong Door):
echo   http://127.0.0.1:8090/unified/residents/NO/2019/4697
echo.
echo ============================================================
echo   Press any key to exit this launcher window.
echo   The 3 service windows will keep running.
echo ============================================================
echo.
pause >nul
