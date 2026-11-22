#!/bin/bash
cd "$(dirname "$0")" || exit 1
export FACTORY_HOST=0.0.0.0
printf '\nスマホ用：Macと同じWi-Fiで、次のアドレスをスマホに入力してください。\n'
for interface in en0 en1; do
 address=$(ipconfig getifaddr "$interface" 2>/dev/null)
 if [ -n "$address" ]; then printf 'http://%s:8765/mobile.html\n' "$address"; fi
done
printf 'WindowsやMacのファイアウォールで接続を拒否した場合は、信頼できる社内Wi-Fi内でPythonの受信接続を許可してください。\n終了：Control+C\n\n'
python3 server.py
