@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在从 ModelScope 下载 VoxCPM2（约 5GB，可断点续传）...
python -m modelscope.cli.cli download --model OpenBMB/VoxCPM2 --local_dir models\VoxCPM2
if errorlevel 1 (
  echo 下载失败，请检查网络或改用: python download_model.py
  pause
) else (
  echo 下载完成，模型位于 models\VoxCPM2
  pause
)
