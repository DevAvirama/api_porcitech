@echo off
REM ==============================================================================
REM Lanzador de Despliegue para Windows 10/11 (CMD / Doble Clic) - PorciTech
REM ==============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] El despliegue finalizo con errores. Revisa los mensajes anteriores.
    pause
    exit /b %ERRORLEVEL%
)
pause

