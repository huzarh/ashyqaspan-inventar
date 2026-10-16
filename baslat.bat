@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 app.py %*
) else (
  python app.py %*
)
if errorlevel 1 (
  echo.
  echo Python 3.8 veya daha yenisi gereklidir.
  echo Python kuruluysa hata mesajini kontrol edin.
)
pause
