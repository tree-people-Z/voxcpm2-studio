@echo off
REM 打包：dist\VoxCPM2Studio\VoxCPM2Studio.exe
REM   build.bat          增量打包（保留 build\ 缓存，第二次快很多）
REM   build.bat clean    先删 build\ dist\ 再打（首次/换依赖时用）
REM   build.bat zip      打完顺手压一个 7z 包（多线程）
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if /i "%~1"=="clean" (
  echo [0/3] 清理 build\ 与 dist\ ...
  if exist build rmdir /s /q build
  if exist dist rmdir /s /q dist
)

echo [1/3] 检查依赖...
python -c "import PyInstaller" 2>nul || python -m pip install pyinstaller || goto :err

echo [2/3] 打包（PyInstaller 单进程分析，主要耗时在扫描与复制；多核帮不上，别指望）...
python -m PyInstaller voxcpm2studio.spec --noconfirm --log-level=WARN || goto :err

echo [3/3] 完成
for %%F in (dist\VoxCPM2Studio\VoxCPM2Studio.exe) do echo   exe: %%~fF

if /i "%~1"=="zip" (
  set "SEVENZ=C:\Program Files\7-Zip\7z.exe"
  if exist "%SEVENZ%" (
    echo 正在压缩（7z 多线程 -mmt=on）...
    "%SEVENZ%" a -tzip -mx=1 -mmt=on -bso0 -bsp0 VoxCPM2Studio-windows-x64.zip .\dist\VoxCPM2Studio\* >nul
    for %%F in (VoxCPM2Studio-windows-x64.zip) do echo   zip: %%~fF  (%%~zF 字节)
  ) else (
    echo 没找到 7-Zip，跳过压缩
  )
)
exit /b 0

:err
echo.
echo 打包失败，请看上面的报错。
exit /b 1
