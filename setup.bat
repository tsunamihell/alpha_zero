@echo off
chcp 65001 >nul
echo =======================================================
echo        AlphaZero / Chess Engine Setup Script
echo =======================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10 or 3.11 and ensure "Add Python to PATH" is checked.
    pause
    exit /b 1
)

echo [*] Python detected. Creating virtual environment (.venv)...
if not exist .venv (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [*] Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet

echo [*] Installing dependencies from requirements.txt...
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo.
echo =======================================================
if not exist lc0\lc0.exe (
    echo [!] Important: The 'lc0' folder needs binaries and models.
    echo Copy the 'lc0' directory from your other system into this folder.
) else (
    echo [*] Lc0 engine found in lc0\ folder!
)
echo.
echo [✓] Setup completed successfully!
echo =======================================================
pause
