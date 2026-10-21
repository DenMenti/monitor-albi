@echo off
title Monitor Albi Pretori
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (
  echo.
  echo Python non e' installato.
  echo Scaricalo da https://www.python.org/downloads/
  echo e durante l'installazione spunta "Add python.exe to PATH".
  echo.
  pause
  exit /b
)

if not exist venv\Scripts\python.exe (
  echo Prima installazione: preparo il programma, ci vuole qualche minuto...
  %PY% -m venv venv
  venv\Scripts\python -m pip install --upgrade pip
  venv\Scripts\python -m pip install -r requirements.txt
)

venv\Scripts\python app.py
pause
