#!/bin/bash
# 夸克网盘资源搜索器 - 一键启动
cd "$(dirname "$0")"
echo "🚀 启动夸克网盘资源搜索器..."
/Users/zilong/.workbuddy/binaries/python/envs/quark_search/bin/python3 -m uvicorn backend:app --host 0.0.0.0 --port 8899 --log-level warning &
sleep 2
echo "✅ 服务已启动: http://localhost:8899"
echo "按 Ctrl+C 停止服务"
wait
