@echo off
chcp 65001 >nul
title 夸克网盘资源搜索器

echo ==================================
echo   夸克网盘资源搜索器 v5
echo   正在启动...
echo ==================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 安装依赖（首次运行）
echo [1/2] 安装依赖...
pip install fastapi uvicorn curl_cffi beautifulsoup4 lxml ddgs -q

REM 启动服务
echo [2/2] 启动服务...
echo.
echo ✅ 服务已启动!
echo 📱 本机访问: http://localhost:8899
echo 📱 局域网访问: 用手机浏览器打开 http://你的电脑IP:8899
echo    (查看IP: 打开cmd输入 ipconfig)
echo.
echo ⚠️ 如需外网访问，请配置路由器端口转发(8899端口)
echo    或使用 Tailscale: https://tailscale.com/download
echo.
echo 按 Ctrl+C 停止服务
echo ==================================

python -m uvicorn backend:app --host 0.0.0.0 --port 8899 --log-level warning

pause
