@echo off
chcp 65001 > nul
echo.
echo  מונה קלוריות - הפעלה
echo ========================
echo.

:: Check Python
python --version > nul 2>&1
if errorlevel 1 (
    echo שגיאה: Python לא מותקן. אנא התקן מ- https://python.org
    pause
    exit /b 1
)

:: Create venv if needed
if not exist "venv" (
    echo יוצר סביבה וירטואלית...
    python -m venv venv
)

:: Activate and install
call venv\Scripts\activate.bat

echo מתקין תלויות...
pip install -r requirements.txt -q

:: Check .env file
if not exist ".env" (
    echo.
    echo אזהרה: קובץ .env לא נמצא.
    echo כדי להשתמש בניתוח תמונות AI, צור קובץ .env עם מפתח ANTHROPIC_API_KEY
    echo ניתן להעתיק מ-.env.example
    echo.
)

echo.
echo פותח את הדפדפן...
start http://localhost:5000

echo הפעלת השרת...
python app.py

pause
