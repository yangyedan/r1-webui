#!/bin/sh
# 一键装成 systemd 服务（Linux）
#   sudo sh deploy/install-systemd.sh [安装目录]
set -e

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-/opt/r1-webui}"
USER_NAME="${SUDO_USER:-$(id -un)}"

echo "==> 安装到 $DEST（运行用户：$USER_NAME）"
mkdir -p "$DEST"
cp -r "$SRC_DIR"/* "$DEST"/ 2>/dev/null || true
rm -rf "$DEST/__pycache__" "$DEST/.git"

if [ ! -f "$DEST/config.json" ]; then
    cp "$DEST/config.example.json" "$DEST/config.json"
    echo "==> 已生成 $DEST/config.json，请先填 llm.api_key 和 media.public_base"
fi

sed -e "s|^User=.*|User=$USER_NAME|" \
    -e "s|^Group=.*|Group=$USER_NAME|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$DEST|" \
    -e "s|^ExecStart=.*|ExecStart=$(command -v python3) $DEST/server.py|" \
    "$DEST/deploy/r1-webui.service" > /etc/systemd/system/r1-webui.service

systemctl daemon-reload
systemctl enable r1-webui
echo "==> 已启用 r1-webui"
echo "   1) 编辑 $DEST/config.json 填好配置"
echo "   2) sudo systemctl restart r1-webui"
echo "   3) curl http://127.0.0.1:8866/health"
