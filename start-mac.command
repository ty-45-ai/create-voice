#!/bin/bash
# Start the app (double-click in Finder). The first run sets up the Python environment.
cd "$(dirname "$0")" || exit 1

fail() {
  echo ""
  echo "エラー: $1"
  echo "マニュアルの「困ったとき」を確認してください。このウィンドウは閉じてかまいません。"
  exit 1
}

# A Python of 3.10 or newer; macOS's own python3 is often 3.9, which this app cannot use.
find_python() {
  for py in python3.13 python3.12 python3.11 python3.10 python3.14 python3; do
    if command -v "$py" >/dev/null 2>&1 \
      && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      echo "$py"
      return
    fi
  done
}

if [ ! -f .env ]; then
  cp .env.example .env
  echo "設定ファイル .env を作りました。"
fi

# (Re)install the parts when missing, half-installed, or when requirements.txt changed.
stamp=".venv/.installed-$(shasum requirements.txt | cut -c1-12)"
if [ ! -f "$stamp" ]; then
  py="$(find_python)"
  [ -n "$py" ] || fail "Python 3.10 以上が見つかりません。python.org から Python 3.13 をインストールしてください。"
  echo "初回の準備をしています（数分かかります）… 使用する Python: $("$py" --version)"
  rm -rf .venv
  "$py" -m venv .venv || fail "Python の仮想環境を作れませんでした。"
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt || fail "部品のインストールに失敗しました。インターネット接続を確認してください。"
  touch "$stamp"
fi

if curl -s -o /dev/null http://127.0.0.1:8000/ 2>/dev/null; then
  echo "アプリはすでに起動しています。管理画面を開きます。"
  open "http://127.0.0.1:8000/admin"
  exit 0
fi

# Open the admin page as soon as the server answers.
(for _ in $(seq 60); do
  sleep 1
  if curl -s -o /dev/null http://127.0.0.1:8000/; then open "http://127.0.0.1:8000/admin"; break; fi
done) &

echo ""
echo "アプリを起動します。終了するときは、このウィンドウで control + C を押してください。"
echo ""
caffeinate -dims .venv/bin/python -m app
