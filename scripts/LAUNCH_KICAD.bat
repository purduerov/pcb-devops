@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Purdue ROV - KiCad Launch System

if not "%~1"=="" cd /d "%~1"
set "TARGET_DIR=%CD%"

echo.
echo ============================================================
echo   🚀 Purdue ROV - KiCad Launch ^& Sync System
echo ============================================================
echo.

REM 1. Git environment configuration. The Git hook path is owned by
REM "rov board bootstrap", which installs the untracked .rov-hooks directory.
echo [1/3] Configuring Git environment...
git config submodule.recurse true >nul 2>&1

REM 2. Prepare the board through the shared bootstrap: rename starter design
REM files, write rov.project.json, add standard library table entries, prepare
REM the library submodule, and install the untracked hook. The bootstrap never
REM resets, stashes, or overwrites local work.
echo [2/3] Preparing board project, library tables, submodule, and hooks...
set "ROV_CLI=%~dp0rov.py"
where python >nul 2>&1
if not errorlevel 1 (
    python "%ROV_CLI%" board bootstrap --project-dir "%TARGET_DIR%" --non-interactive
    exit /b !ERRORLEVEL!
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%ROV_CLI%" board bootstrap --project-dir "%TARGET_DIR%" --non-interactive
    exit /b !ERRORLEVEL!
)
echo Python is required to prepare this board. Open KiCad manually or install Python.
exit /b 2

REM 3. Locate and Launch KiCad Project
echo [3/3] Launching KiCad project...
set "PROJ_FILE="
for %%f in (*.kicad_pro) do (
    set "PROJ_FILE=%%f"
    goto :found_proj
)

:found_proj
if "%PROJ_FILE%"=="" (
    echo.
    echo ============================================================
    echo ⚠️ No .kicad_pro project file found in %TARGET_DIR%!
    echo ============================================================
    pause
    exit /b 1
)

echo      Opening: %PROJ_FILE%
echo.

REM Try system association first
start "" "%PROJ_FILE%" >nul 2>&1
if %ERRORLEVEL% EQU 0 exit /b 0

REM Fallback to standard installation paths
if exist "%ProgramFiles%\KiCad\10.0\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\10.0\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0
if exist "%ProgramFiles%\KiCad\9.0\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\9.0\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0
if exist "%ProgramFiles%\KiCad\8.0\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\8.0\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0
if exist "%ProgramFiles%\KiCad\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0

echo ⚠️ KiCad opened via file association.
exit /b 0