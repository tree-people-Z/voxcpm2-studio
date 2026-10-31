@echo off
REM 打包成 dist\VoxCPM2Studio\VoxCPM2Studio.exe（模型不打包，首次启动在界面里下载）
setlocal
cd /d "%~dp0"

echo [1/3] 安装依赖...
python -m pip install -r requirements.txt pyinstaller || goto :err

echo [2/3] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 打包...
python -m PyInstaller voxcpm2studio.spec --noconfirm || goto :err

echo.
echo 完成：dist\VoxCPM2Studio\VoxCPM2Studio.exe
echo 提示：首次启动会在界面里提示下载约 4.6GB 模型到 exe 同级的 models\ 目录。
exit /b 0

:err
echo.
echo 打包失败，请看上面的报错。
exit /b 1
