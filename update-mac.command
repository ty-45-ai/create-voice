#!/bin/bash
# Update to the newest version from GitHub. Stop the app first (control + C in its window).
cd "$(dirname "$0")" || exit 1

if curl -s -o /dev/null http://127.0.0.1:8000/ 2>/dev/null; then
  echo "アプリが起動したままです。アプリのウィンドウで control + C を押して止めてから、もう一度実行してください。"
  exit 1
fi

echo "最新版を取得しています…"
if ! git pull --ff-only; then
  echo ""
  echo "エラー: 最新版を取得できませんでした。"
  echo "インターネット接続と、GitHub のアクセストークン（期限切れでないか）を確認してください。"
  exit 1
fi
echo ""
echo "更新が完了しました。start-mac.command で起動してください（必要な部品は起動時に自動で入れ直します）。"
