'use strict';
(() => {
  const buttons = [...document.querySelectorAll('[data-backup-save]')];
  const status = document.getElementById('backupStatus');
  let busy = false;
  const say = (text, failed = false) => {
    status.textContent = text;
    status.classList.toggle('error', failed);
  };
  async function saveBackup() {
    if (busy) return;
    busy = true;
    buttons.forEach(button => { button.disabled = true; button.textContent = '保存データを準備中…'; });
    say('登録済みのデータをまとめています。');
    try {
      const response = await fetch('/api/db-backup', {cache: 'no-store'});
      if (!response.ok) throw new Error('保存データを取得できませんでした。アプリの起動画面を確認して、もう一度お試しください。');
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (String.fromCharCode(...bytes.slice(0, 16)) !== 'SQLite format 3\u0000') {
        throw new Error('正しいバックアップを受け取れませんでした。アプリを起動しているか確認してください。');
      }
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename="?(warehouse-\d{8}-\d{6}-\d{6}\.sqlite3)"?/);
      const filename = match ? match[1] : 'warehouse-backup-' + Date.now() + '.sqlite3';
      const url = URL.createObjectURL(new Blob([bytes], {type: 'application/octet-stream'}));
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.append(link);
      try { link.click(); } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); }
      say('ダウンロードを開始しました：' + filename + '。ブラウザのダウンロード一覧で保存完了を確認してください。');
    } catch (error) {
      say(error instanceof TypeError ? '接続できません。アプリの起動画面と通信状態を確認して、もう一度お試しください。' : error.message, true);
    } finally {
      busy = false;
      buttons.forEach(button => { button.disabled = false; button.textContent = 'データを保存'; });
    }
  }
  buttons.forEach(button => button.addEventListener('click', saveBackup));
})();
