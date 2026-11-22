"""JFS-B inventory operation support; not a certification system."""
import json, math
from datetime import datetime

def migrate(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=17:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 for col in ['supplier','supplier_lot','delivery_ref']:
  db.execute("ALTER TABLE receipts ADD COLUMN "+col+" TEXT NOT NULL DEFAULT ''")
 db.execute("ALTER TABLE receipts ADD COLUMN quality_status TEXT NOT NULL DEFAULT '未確認'")
 db.execute('ALTER TABLE receipts ADD COLUMN quality_hold INTEGER NOT NULL DEFAULT 0')
 db.execute("ALTER TABLE movements ADD COLUMN trace_ref TEXT NOT NULL DEFAULT ''")
 db.execute('''CREATE TABLE inspections(id INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id INTEGER NOT NULL REFERENCES receipts(id),status TEXT NOT NULL,actor TEXT NOT NULL,staff_id INTEGER NOT NULL REFERENCES staff(id),created_at TEXT NOT NULL,inspected_at TEXT NOT NULL,reason TEXT NOT NULL,details_json TEXT NOT NULL,before_json TEXT NOT NULL,request_id TEXT NOT NULL,UNIQUE(request_id,receipt_id))''')
 db.execute('CREATE INDEX idx_inspection_receipt ON inspections(receipt_id)')
 db.execute('PRAGMA user_version=17')

def reset(db,rid):
 db.execute("UPDATE receipts SET quality_status='未確認',edit_revision=edit_revision+1 WHERE id=?",(rid,))

def review(db,d,h,now):
 key=h.text(d,'request_id',80,True)
 db.execute('BEGIN IMMEDIATE')
 prior=db.execute('SELECT receipt_id FROM inspections WHERE request_id=?',(key,)).fetchall()
 if prior:return {'ok':True,'count':len(prior)}
 sid,actor=h.selected_staff(db,d)
 targets=d.get('targets')
 if not isinstance(targets,list) or not 1<=len(targets)<=200:raise ValueError('検品する入庫を1〜200件選んでください。')
 if any(not isinstance(t,dict) for t in targets):raise ValueError('対象を選び直してください。')
 if len({str(t.get('id')) for t in targets})!=len(targets):raise ValueError('検品対象が重複しています。')
 status=h.text(d,'status',10,True)
 if status not in ['合格','保留','不適合']:raise ValueError('判定を選んでください。')
 reason=h.text(d,'reason',200,True)
 details={k:h.text(d,k,200,False) for k in ['spec_ref','supplier_approval','instrument_ref','packaging','label_check','action','cause','verification']}
 inspected=h.text(d,'inspected_at',19,True)
 try: stamp=datetime.fromisoformat(inspected)
 except ValueError:raise ValueError('検品日時を入力してください。')
 if stamp.tzinfo is not None or stamp>datetime.now() or stamp.year<2000:raise ValueError('検品日時は2000年以降、現在までの日時を入力してください。')
 for k in ['temp_min','temp_max']:
  v=d.get(k,'')
  try:n=None if v=='' or v is None else float(v)
  except (ValueError,TypeError):raise ValueError('温度基準を数値で入力してください。')
  if n is not None and (not math.isfinite(n) or not -100<=n<=100):raise ValueError('温度基準の範囲を確認してください。')
  details[k]=n
 if status=='合格':
  if any(not details[k] for k in ['spec_ref','supplier_approval','instrument_ref']):raise ValueError('規格書・仕入先承認記録・温度計確認記録を入力してください。')
  if details['packaging']!='適合' or details['label_check']!='適合':raise ValueError('包装と表示・数量の確認が適合の場合のみ合格にできます。')
  if details['temp_min'] is None or details['temp_max'] is None or details['temp_min']>details['temp_max']:raise ValueError('承認済み規格書の温度基準の下限・上限を入力してください。')
 rows=[]
 for target in targets:
  found=h.stock(db,int(target.get('id',0)))
  if not found:raise ValueError('対象が削除済みです。検品一覧を更新してください。')
  row=found[0]
  if target.get('edit_revision')!=row['edit_revision'] or target.get('revision')!=row['revision']:raise ValueError('在庫または入庫情報が変わりました。対象を選び直して再確認してください。')
  if stamp.date().isoformat()<row['received']:raise ValueError('検品日は入庫日以降を入力してください。')
  if status=='合格':
   if not row['supplier'] or not row['supplier_lot']:raise ValueError('入庫の変更・修正で仕入先と仕入先ロットを記入してください。')
   if row['days_left']<0:raise ValueError('期限切れの在庫は合格にできません。')
   try:temperature=float(row['temperature_band'].replace('℃',''))
   except ValueError:raise ValueError('数値の入庫温度が未記録です。入庫の変更・修正で記録してください。')
   if not details['temp_min']<=temperature<=details['temp_max']:raise ValueError('入庫温度が設定した基準外です。合格にできません。')
   if row['quality_hold'] and any(not details[k] for k in ['cause','action','verification']):raise ValueError('保留・不適合を解除するには原因、処置、検証記録を入力してください。')
  rows.append(row)
 for row in rows:
  db.execute('INSERT INTO inspections(receipt_id,status,actor,staff_id,created_at,inspected_at,reason,details_json,before_json,request_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(row['id'],status,actor,sid,now,inspected,reason,json.dumps(details,ensure_ascii=False),json.dumps(row,ensure_ascii=False),key))
  db.execute('UPDATE receipts SET quality_status=?,quality_hold=?,edit_revision=edit_revision+1 WHERE id=?',(status,0 if status=='合格' else 1,row['id']))
 return {'ok':True,'count':len(rows)}

def export(db,h):
 db.execute('BEGIN')
 tables=['products','staff','locations','units','receipts','movements','inspections','corrections','task_edits','deletions']
 out={'app_version':'3.0.2','schema_version':17,'exported_at':datetime.now().astimezone().isoformat(),'scope':'全記録（取消・削除済みを含む）。JSONは閲覧・保全用で、復元はSQLiteバックアップを使用。'}
 for table in tables:
  out[table]=[dict(r) for r in db.execute('SELECT * FROM '+table)]
  if table=='receipts':
   for r in out[table]:r['code']=h.code_for(r['id'])
 out['stock']=h.stock(db)
 return out
