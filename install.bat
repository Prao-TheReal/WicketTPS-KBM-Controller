@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Controller Cam Helper - Setup

:: -----------------------------
:: Step 0: Auto-Elevate to Admin
:: -----------------------------
:: We need Admin rights to install Drivers (ViGEmBus) and Python globally.
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo ========================================================
    echo  Requesting Administrator Privileges...
    echo  (Needed to install Drivers and Python)
    echo ========================================================
    echo.
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo ===============================================
echo   Controller Cam Helper - Setup (No HidHide)
echo ===============================================
echo.

:: -----------------------------
:: Step 1: Find Python
:: -----------------------------
echo [1/4] Checking for Python...
set "PYEXE="

where py >nul 2>&1
if "%errorlevel%"=="0" (
  set "PYEXE=py -3"
) else (
  where python >nul 2>&1
  if "%errorlevel%"=="0" (
    set "PYEXE=python"
  )
)

:: If not found, Install via Winget
if not defined PYEXE (
  echo Python not found. Installing via Winget...
  echo.
  where winget >nul 2>&1
  if "%errorlevel%"=="0" (
    winget install -e --id Python.Python.3 --accept-package-agreements --accept-source-agreements --scope machine
    
    :: Refresh Path for current session so we can use it immediately
    set "PATH=%PATH%;%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts"
    set "PYEXE=python"
  ) else (
    echo ERROR: Winget not found. Opening download page...
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
  )
)

echo Using Python: %PYEXE%
echo.

:: -----------------------------
:: Step 2: Install Libraries
:: -----------------------------
echo [2/4] Installing Python Libraries (pygame, vgamepad)...
%PYEXE% -m pip install --upgrade pip
%PYEXE% -m pip install --upgrade pygame vgamepad
if not "%errorlevel%"=="0" (
  echo ERROR: Failed to install python packages.
  pause
  exit /b 1
)

:: -----------------------------
:: Step 3: Install ViGEmBus Driver (REQUIRED)
:: -----------------------------
echo.
echo [3/4] Checking for Virtual Controller Driver (ViGEmBus)...
if exist "C:\Program Files\Nefarius Software Solutions\ViGEm Bus Driver\ViGEmBus.sys" (
    echo ViGEmBus is already installed.
) else (
    echo ViGEmBus NOT found. Installing via Winget...
    winget install -e --id Nefarius.ViGEmBus --accept-package-agreements --accept-source-agreements
    if not "%errorlevel%"=="0" (
        echo Winget failed. Opening ViGEmBus download page...
        start "" "https://github.com/ViGEm/ViGEmBus/releases/latest"
    )
)

:: -----------------------------
:: Step 4: Generate 'run.bat'
:: -----------------------------
echo.
echo [4/4] Generating 'run.bat' launcher...
(
echo @echo off
echo :: Force the script to run from the folder it is saved in
echo cd /d "%%~dp0"
echo.
echo echo Starting Controller Helper...
echo :: Use the python command we found earlier
echo %PYEXE% camera_relative_stick_pygame.py
echo.
echo if %%errorlevel%% neq 0 pause
) > run.bat

echo.
echo ===============================================
echo        SETUP COMPLETE!
echo ===============================================
echo 1. IMPORTANT: If using a Controller, manually hide it
echo    using HidHide so the game does not see double inputs.
echo 2. Run 'run.bat' to start the script!
echo.
pause