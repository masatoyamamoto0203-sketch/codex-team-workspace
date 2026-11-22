#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
 echo 'Python 3.9以上が必要です。https://www.python.org/downloads/ からインストールしてください。'
 read -r -p 'Enterで終了'
 exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
 echo 'Python 3.9以上が必要です。表示された画面をサポート担当へお送りください。'
 read -r -p 'Enterで終了'
 exit 1
fi
python3 server.py
read -r -p 'Enterで終了'
