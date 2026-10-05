@echo off
setlocal enabledelayedexpansion
title Build jsub-translator EXE

echo.
echo ==============================================
echo   Build jsub-translator EXE  (PyInstaller)
echo ==============================================
echo.

cd /d "%~dp0"

rem ---- 一律使用项目内的 Python 环境，不用 C 盘的 ----
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" goto :no_venv

rem ---- 临时目录也放项目内，避免构建过程写 C 盘 ----
set "TEMP=%~dp0_build\tmp"
set "TMP=%~dp0_build\tmp"
set "PIP_CACHE_DIR=%~dp0.pip-cache"
if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"

echo [1/4] 环境: %PY%
"%PY%" -c "import PyInstaller, ctranslate2; print('      PyInstaller', PyInstaller.__version__, '| ctranslate2', ctranslate2.__version__)"
if %errorlevel% neq 0 goto :bad_env

echo [1b/4] 检查说话人识别模型 ...
"%PY%" -c "import sys; sys.path.insert(0,'.'); from src.config import validate_diarization; ok,why=validate_diarization(); print('      ',why); sys.exit(0)"
if %errorlevel% neq 0 goto :build_failed

echo [2/4] 检查 CUDA 运行库 cuBLAS ...
"%PY%" -c "import os,sys,ctranslate2; d=os.path.dirname(ctranslate2.__file__); miss=[n for n in ('cublas64_12.dll','cublasLt64_12.dll') if not os.path.isfile(os.path.join(d,n))]; print('[build] ctranslate2 dir: '+d); print('[build] missing: '+(', '.join(miss) if miss else 'none')); sys.exit(1 if miss else 0)"
if %errorlevel% neq 0 goto :no_cublas

echo [2b/4] 检查 src\ 下没有会被误打包的备份文件 ...
rem spec 里 datas 用的是 ('src','src')，整个目录都会被收进去。
rem 留下 src\config.py.bak-xxx 这类文件会一起打进 EXE，白白增大体积。
"%PY%" -c "import glob,os,sys; bad=[p for p in glob.glob('src/**/*',recursive=True) if os.path.isfile(p) and (p.endswith(('.bak','.orig','.tmp','.py.old')) or '.bak-' in p or '.orig-' in p)]; print('[build] stray backups: '+(', '.join(bad) if bad else 'none')); sys.exit(1 if bad else 0)"
if %errorlevel% neq 0 goto :stray_backup

echo [3/4] 清理旧构建 ...
if exist "_build\work" rmdir /s /q "_build\work"
if exist "_build\dist" rmdir /s /q "_build\dist"

echo [4/4] 打包 EXE ... 含约 600MB CUDA 库，约需 5-15 分钟
echo.
"%PY%" -m PyInstaller --noconfirm --distpath "_build\dist" --workpath "_build\work" "jsub-translator.spec"
if %errorlevel% neq 0 goto :build_failed

rem ---- 安全替换：旧 EXE 备份为 .bak-prev，再放入新的 ----
rem 注意：绝不 rmdir 整个 dist\，里面有 output\ 成品、models\ 和 .env
if exist "dist\jsub-translator.exe" (
    if exist "dist\jsub-translator.exe.bak-prev" del /q "dist\jsub-translator.exe.bak-prev"
    move /y "dist\jsub-translator.exe" "dist\jsub-translator.exe.bak-prev" >nul
)
move /y "_build\dist\jsub-translator.exe" "dist\jsub-translator.exe" >nul
if %errorlevel% neq 0 goto :swap_failed

echo.
echo ==============================================
echo   打包完成
echo.
echo   EXE: dist\jsub-translator.exe
echo.
echo   启动请用 dist\run.bat
echo   它把 TEMP 重定向到 E 盘，避免 PyInstaller 每次启动
echo   往 C 盘解包约 850MB
echo.
echo   dist\ 下已有的 output\ / models\ / .env 均未被改动
echo ==============================================
echo.
pause
exit /b 0

:no_venv
echo   [ERROR] 找不到项目环境:
echo     %PY%
echo.
echo   本项目不使用 C 盘的 Python 环境。创建方式:
echo     <your Python 3.13+ interpreter> -m venv "%~dp0.venv"
echo   然后把 site-packages 从旧环境复制过来，或 pip install -r requirements.txt
echo.
pause
exit /b 1

:bad_env
echo   [ERROR] 该环境缺少 PyInstaller 或 ctranslate2
pause
exit /b 1

:no_cublas
echo.
echo   [ERROR] 缺少 cuBLAS，打出来的 EXE 无法使用 GPU 识别。
echo           请把 cublas64_12.dll 和 cublasLt64_12.dll 放进上面的 ctranslate2 目录。
echo           可从 _cuda_dlls\ 复制。
echo.
pause
exit /b 1

:build_failed
echo.
echo   [ERROR] 打包失败
pause
exit /b 1

:swap_failed
echo   [ERROR] 无法替换 dist\jsub-translator.exe
echo           请确认程序未在运行后重试。
pause
exit /b 1

:stray_backup
echo.
echo   [ERROR] src\ 下有备份/临时文件会被一起打进 EXE
echo           请把它们移到 _backups\ 下再重新构建。
echo           （spec 的 datas 里写的是 ('src','src')，整个目录都会被收进去）
echo.
pause
exit /b 1
