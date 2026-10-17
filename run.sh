#!/bin/sh
# Linux / macOS 启动脚本
cd "$(dirname "$0")" || exit 1

if [ ! -f config.json ]; then
    cp config.example.json config.json
    echo "[!] 已生成 config.json，请先填写 llm.api_key 与 media.public_base 后重新运行。"
    exit 1
fi

exec python3 server.py "$@"
