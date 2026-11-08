@echo off
setlocal

rem Ejecutar desde el directorio del proyecto.
pushd "%~dp0" || exit /b 1

if exist ".venv\Scripts\python.exe" goto venv

where py >nul 2>nul
if not errorlevel 1 goto pylauncher

where python >nul 2>nul
if not errorlevel 1 goto python

echo Python 3 no esta disponible.
set "APP_RESULT=1"
goto done

:venv
".venv\Scripts\python.exe" app.py %*
set "APP_RESULT=%ERRORLEVEL%"
goto done

:pylauncher
py -3 app.py %*
set "APP_RESULT=%ERRORLEVEL%"
goto done

:python
python app.py %*
set "APP_RESULT=%ERRORLEVEL%"

:done
popd
exit /b %APP_RESULT%