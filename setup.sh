#!/bin/bash
# ThreadsRadar 一鍵安裝
set -e
cd "$(dirname "$0")"

echo "📡 ThreadsRadar 串文雷達 - 安裝中..."

# 檢查 Python3
if ! command -v python3 &>/dev/null; then
  echo "❌ 請先安裝 Python3: https://www.python.org/downloads/"
  exit 1
fi

# 安裝 Python 依賴
echo "📦 安裝 Python 依賴..."
pip3 install -r requirements.txt

# 安裝 Playwright 瀏覽器
echo "🌐 安裝 Playwright 瀏覽器..."
python3 -m playwright install chromium

# 建立 .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "⚠️  請編輯 .env 填入你的 Threads 帳號密碼："
  echo "    open .env"
  echo ""
fi

echo ""
echo "✅ 安裝完成！"
echo ""
echo "使用方式："
echo "  1. 編輯 .env 填入 Threads 帳密"
echo "  2. 執行 ./run.sh 啟動"
echo "  3. 瀏覽器會自動開啟，按按鈕開始爬取"
