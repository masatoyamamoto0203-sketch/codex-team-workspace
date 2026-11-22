#!/usr/bin/env python3
"""Single-workstation warehouse label registry. Python 3.9+; standard library only."""
import tempfile
import jfs
import json, sqlite3, os, sys, secrets, socket, webbrowser, threading, io, csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from datetime import datetime, date
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('FACTORY_DATA_DIR',str(ROOT/'data')))
DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'warehouse.sqlite3'
HOST=os.environ.get('FACTORY_HOST','127.0.0.1')
PORT=int(os.environ.get('FACTORY_PORT','8765'))
TOKEN=secrets.token_urlsafe(24)
def connect():
 db=sqlite3.connect(DB,timeout=15);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db
def init_v1():
 with connect() as db:
  db.executescript('''CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, spec TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', department TEXT NOT NULL DEFAULT '', processing TEXT NOT NULL, jan TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id), product_name TEXT NOT NULL, spec TEXT NOT NULL, unit TEXT NOT NULL, received TEXT NOT NULL, expires TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity>0), location TEXT NOT NULL, created_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE);
''')
SCALE=1000000
def amount(v,zero=False):
 try:q=Decimal(str(v))
 except InvalidOperation:raise ValueError('数量は数値で入力してください。')
 if not q.is_finite() or q<0 or (not zero and q==0) or q>1000000000:raise ValueError('数量の範囲が正しくありません。')
 if q*SCALE!=(q*SCALE).to_integral_value():raise ValueError('数量は小数点以下6桁までです。')
 return int(q*SCALE)
def init():
 if DB.exists():
  with connect() as db:
   v=db.execute('PRAGMA user_version').fetchone()[0]
   if v<17:
    target=DATA/('v'+str(v)+'-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
    with sqlite3.connect(target) as backup:db.backup(backup)
 init_v1()
 with connect() as db:
  if db.execute('PRAGMA user_version').fetchone()[0]>=2:
   init_staff(db);init_notes(db);init_corrections(db);init_task_edits(db);init_dates(db);init_deletions(db);init_locations(db);init_temperature(db);init_cases(db);init_case_labels(db);init_units(db);init_purchase(db);init_product_staff(db);init_case_moves(db);jfs.migrate(db);return
  db.execute('BEGIN IMMEDIATE')
  db.execute("ALTER TABLE products ADD COLUMN minimum_u INTEGER NOT NULL DEFAULT 0")
  db.execute("ALTER TABLE receipts ADD COLUMN initial_u INTEGER NOT NULL DEFAULT 0")
  db.execute("ALTER TABLE receipts ADD COLUMN actor TEXT NOT NULL DEFAULT 'V1移行・担当未記録'")
  db.execute("ALTER TABLE receipts ADD COLUMN entry_kind TEXT NOT NULL DEFAULT '入庫'")
  for row in db.execute('SELECT id,quantity FROM receipts').fetchall():
   db.execute('UPDATE receipts SET initial_u=? WHERE id=?',(amount(row['quantity']),row['id']))
  db.execute("CREATE TABLE movements(id INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id INTEGER NOT NULL REFERENCES receipts(id), kind TEXT NOT NULL, delta_u INTEGER NOT NULL, before_u INTEGER NOT NULL, after_u INTEGER NOT NULL CHECK(after_u>=0), actor TEXT NOT NULL, reason TEXT NOT NULL, department TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE)")
  db.execute('CREATE INDEX idx_movements_receipt ON movements(receipt_id)')
  db.execute('PRAGMA user_version=2')
  init_staff(db)
  init_notes(db)
  init_corrections(db)
  init_task_edits(db)
  init_dates(db)
  init_deletions(db)
  init_locations(db)
  init_temperature(db)
  init_cases(db)
  init_case_labels(db)
  init_units(db)
  init_purchase(db)
  init_product_staff(db)
  init_case_moves(db)
  jfs.migrate(db)
def init_staff(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=3:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute("CREATE TABLE staff(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)))")
 db.execute('ALTER TABLE receipts ADD COLUMN staff_id INTEGER REFERENCES staff(id)')
 db.execute('ALTER TABLE movements ADD COLUMN staff_id INTEGER REFERENCES staff(id)')
 db.execute('PRAGMA user_version=3')
def init_notes(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=4:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute("ALTER TABLE receipts ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
 db.execute('PRAGMA user_version=4')
def init_corrections(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=5:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute('ALTER TABLE receipts ADD COLUMN edit_revision INTEGER NOT NULL DEFAULT 0')
 db.execute('CREATE TABLE corrections(id INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id INTEGER NOT NULL REFERENCES receipts(id),before_json TEXT NOT NULL,after_json TEXT NOT NULL,actor TEXT NOT NULL,staff_id INTEGER NOT NULL REFERENCES staff(id),reason TEXT NOT NULL,created_at TEXT NOT NULL,request_id TEXT NOT NULL UNIQUE)')
 db.execute('PRAGMA user_version=5')
def init_task_edits(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=6:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute('CREATE TABLE task_edits(id INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT NOT NULL,target_id INTEGER NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,request_id TEXT NOT NULL UNIQUE)')
 db.execute('PRAGMA user_version=6')
def init_dates(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=7:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute("ALTER TABLE receipts ADD COLUMN manufactured TEXT NOT NULL DEFAULT ''")
 db.execute('CREATE TABLE locations(name TEXT PRIMARY KEY)')
 db.execute('PRAGMA user_version=7')
def locations(db):
 return [r[0] for r in db.execute("SELECT name FROM locations UNION SELECT location FROM products WHERE location!='' UNION SELECT location FROM receipts WHERE location!='' ORDER BY 1")]
def anniversary(value):
 day=date.fromisoformat(value)
 try:return day.replace(year=day.year+1).isoformat()
 except ValueError:return day.replace(year=day.year+1,day=28).isoformat()
def init_deletions(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=8:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 for table in ['products','staff','receipts','movements']:db.execute('ALTER TABLE '+table+' ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
 db.execute('CREATE TABLE deletions(id INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT NOT NULL,target_id INTEGER NOT NULL,snapshot TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,effect TEXT NOT NULL,created_at TEXT NOT NULL,request_id TEXT NOT NULL UNIQUE)')
 db.execute('PRAGMA user_version=8')
def init_locations(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=9:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute('ALTER TABLE locations ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
 db.execute("INSERT OR IGNORE INTO locations(name) SELECT location FROM products WHERE location!=''")
 db.execute("INSERT OR IGNORE INTO locations(name) SELECT location FROM receipts WHERE location!=''")
 db.execute('PRAGMA user_version=9')
def init_temperature(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=10:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute("ALTER TABLE receipts ADD COLUMN temperature_band TEXT NOT NULL DEFAULT ''")
 db.execute('PRAGMA user_version=10')
def init_cases(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=11:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute('ALTER TABLE products ADD COLUMN case_size INTEGER NOT NULL DEFAULT 0')
 db.execute('ALTER TABLE receipts ADD COLUMN case_size INTEGER NOT NULL DEFAULT 0')
 db.execute('ALTER TABLE receipts ADD COLUMN case_count INTEGER NOT NULL DEFAULT 0')
 db.execute('PRAGMA user_version=11')
def init_case_labels(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=12:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute("ALTER TABLE receipts ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
 db.execute('ALTER TABLE receipts ADD COLUMN case_seq INTEGER NOT NULL DEFAULT 0')
 db.execute('ALTER TABLE receipts ADD COLUMN case_total INTEGER NOT NULL DEFAULT 0')
 db.execute('CREATE INDEX idx_receipt_batch ON receipts(batch_id)')
 db.execute('PRAGMA user_version=12')
def init_units(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=13:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute('CREATE TABLE units(name TEXT PRIMARY KEY,active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)))')
 for name in ['袋','個','本','kg','箱','ケース']:
  db.execute('INSERT OR IGNORE INTO units(name) VALUES(?)',(name,))
 db.execute("INSERT OR IGNORE INTO units(name) SELECT DISTINCT unit FROM products WHERE unit!=''")
 db.execute("INSERT OR IGNORE INTO units(name) SELECT DISTINCT unit FROM receipts WHERE unit!=''")
 db.execute('PRAGMA user_version=13')
def init_purchase(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=14:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute('ALTER TABLE receipts ADD COLUMN purchase_yen INTEGER CHECK(purchase_yen IS NULL OR purchase_yen BETWEEN 0 AND 1000000000000)')
 db.execute('PRAGMA user_version=14')
def init_product_staff(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=15:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute('ALTER TABLE products ADD COLUMN staff_id INTEGER REFERENCES staff(id)')
 db.execute("ALTER TABLE products ADD COLUMN actor TEXT NOT NULL DEFAULT ''")
 db.execute('PRAGMA user_version=15')
def init_case_moves(db):
 if db.execute('PRAGMA user_version').fetchone()[0]>=16:return
 if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
 db.execute("ALTER TABLE movements ADD COLUMN quantity_mode TEXT NOT NULL DEFAULT 'バラ'")
 db.execute('ALTER TABLE movements ADD COLUMN input_u INTEGER')
 db.execute('ALTER TABLE movements ADD COLUMN input_loose_u INTEGER NOT NULL DEFAULT 0')
 db.execute('ALTER TABLE movements ADD COLUMN case_size INTEGER NOT NULL DEFAULT 0')
 db.execute('PRAGMA user_version=16')
def purchase_yen(value):
 if value is None or value=='':return None
 if isinstance(value,bool) or not str(value).isdigit():raise ValueError('仕入れ金額は0以上の円単位の整数で入力してください。')
 n=int(value)
 if n>1000000000000:raise ValueError('仕入れ金額は1兆円以下にしてください。')
 return n
def active_units(db):
 return [r[0] for r in db.execute('SELECT name FROM units WHERE active=1 ORDER BY name')]
def case_size(value):
 if isinstance(value,bool) or not str(value).isdigit():raise ValueError('1ケースの入数は0以上の整数で入力してください。')
 n=int(value)
 if n>1000000:raise ValueError('1ケースの入数は100万以下で入力してください。')
 return n
TEMPERATURE_VALUES=[format(Decimal(i)/2,'f').rstrip('0').rstrip('.')+'℃' if i%2 else str(i//2)+'℃' for i in range(40,-41,-1)]
def temperature_band(d,previous=None):
 value=text(d,'temperature_band',10,False)
 if previous is not None and value==previous:return value
 if value not in TEMPERATURE_VALUES:raise ValueError('入庫時温度は20℃から−20℃まで、0.5℃刻みの選択肢から選んでください。')
 return value
def locations(db,active_only=True):
 where="WHERE active=1" if active_only else ""
 return [r[0] for r in db.execute("SELECT name FROM locations "+where+" ORDER BY name")]
def selected_staff(db,d):
 raw=d.get('staff_id')
 if not isinstance(raw,(str,int)) or isinstance(raw,bool) or not str(raw).isdigit():raise ValueError('担当者を選択してください。未登録の場合は「担当者設定」で名前を登録してください。')
 row=db.execute('SELECT * FROM staff WHERE id=? AND active=1 AND deleted=0',(int(raw),)).fetchone()
 if not row:raise ValueError('この担当者は選択できません。ページを再読み込みして担当者を選び直してください。')
 return row['id'],row['name']
def add_months(day,months):
 import calendar
 y,m=divmod(day.year*12+day.month-1+months,12)
 return date(y,m+1,min(day.day,calendar.monthrange(y,m+1)[1]))
def expiry_status(expires,today=None):
 today=today or date.today();end=date.fromisoformat(expires)
 if end<today:return 'expired'
 if end<add_months(today,3):return 'red'
 if end<=add_months(today,6):return 'yellow'
 return 'normal'
def stock(db,rid=None):
 sql="""SELECT r.*,p.department,p.processing,p.minimum_u,
 r.initial_u+COALESCE(m.delta_u,0) AS balance_u,COALESCE(m.revision,0) AS revision
 FROM receipts r JOIN products p ON p.id=r.product_id
 LEFT JOIN (SELECT receipt_id,SUM(delta_u) delta_u,MAX(id) revision FROM movements GROUP BY receipt_id) m ON m.receipt_id=r.id"""
 sql+=' WHERE r.deleted=0'
 if rid is not None:sql+=' AND r.id=?'
 sql+=' ORDER BY r.expires,r.id'
 rows=db.execute(sql,(rid,) if rid is not None else ()).fetchall();out=[]
 for row in rows:
  r=receipt(row);r['balance']=r['balance_u']/SCALE;r['minimum']=r['minimum_u']/SCALE
  r['days_left']=(date.fromisoformat(r['expires'])-date.today()).days
  r['expiry_status']=expiry_status(r['expires'])
  out.append(r)
 return out
def ean(base):return base+str((-sum(int(v)*(1 if i%2==0 else 3) for i,v in enumerate(base)))%10)
def code_for(i):return ean('20'+str(i+1).zfill(10))
def receipt(row):
 r=dict(row);r['code']=code_for(r['id']);return r
def text(d,k,maxlen=100,required=False):
 label={'location':'保管場所','name':'名前','unit':'単位','supplier':'仕入先','supplier_lot':'仕入先ロット','delivery_ref':'納品書番号','trace_ref':'製造ロット・出荷伝票番号','spec_ref':'規格書番号・版','department':'担当部署'}.get(k,k)
 v=d.get(k,'');
 if not isinstance(v,str):raise ValueError(label+'は文字で入力してください。')
 v=v.strip()
 if len(v)>maxlen or (required and not v):raise ValueError(label+'の入力を確認してください。')
 return v
def dates(d,k):
 v=text(d,k,10,True)
 try: parsed=date.fromisoformat(v)
 except ValueError:raise ValueError('日付を正しく入力してください。')
 if parsed.isoformat()!=v or not 2000<=parsed.year<=2199:raise ValueError('日付は2000〜2199年で入力してください。')
 return v
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'static'),**kw)
 def end_headers(self):
  self.send_header('Cache-Control','no-store');super().end_headers()
 def log_message(self,fmt,*args):pass
 def response(self,data,status=200):
  b=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path).path
  try:
   if p=='/api/db-backup':
    with tempfile.TemporaryDirectory() as folder:
     target=Path(folder)/'warehouse.sqlite3'
     with connect() as db,sqlite3.connect(target) as dest:db.backup(dest)
     b=target.read_bytes()
    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename=warehouse-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
   if p=='/api/jfs-export':
    with connect() as db:return self.response(jfs.export(db,sys.modules[__name__]))
   if p=='/api/inspections':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM inspections ORDER BY id DESC LIMIT 1000')])
   if p=='/api/session':return self.response({'token':TOKEN,'version':17})
   if p.startswith('/api/edit-target/'):
    parts=p.split('/');table={'product':'products','staff':'staff','movement':'movements','receipt':'receipts'}.get(parts[-2])
    if not table:return self.response({'error':'対象が見つかりません。'},404)
    with connect() as db:row=db.execute('SELECT * FROM '+table+' WHERE id=? AND deleted=0',(int(parts[-1]),)).fetchone()
    return self.response(dict(row) if row else {'error':'対象が見つかりません。'},200 if row else 404)
   if p=='/api/deletions':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM deletions ORDER BY id DESC LIMIT 1000')])
   if p=='/api/task-edits':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM task_edits ORDER BY id DESC LIMIT 1000')])
   if p=='/api/corrections':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM corrections ORDER BY id DESC LIMIT 1000')])
   if p=='/api/units':
    with connect() as db:return self.response(active_units(db))
   if p=='/api/unit-settings':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM units ORDER BY active DESC,name')])
   if p=='/api/locations':
    with connect() as db:return self.response(locations(db))
   if p=='/api/location-settings':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM locations ORDER BY active DESC,name')])
   if p=='/api/staff':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM staff WHERE deleted=0 ORDER BY active DESC,id')])
   if p=='/api/stock':
    with connect() as db:return self.response(stock(db))
   if p=='/api/movements':
    with connect() as db:
     rows=[dict(r) for r in db.execute('SELECT m.*,r.product_name,r.unit FROM movements m JOIN receipts r ON r.id=m.receipt_id WHERE m.deleted=0 AND r.deleted=0 ORDER BY m.id DESC LIMIT 1000')]
    for r in rows:r['code']=code_for(r['receipt_id']);r['delta']=r['delta_u']/SCALE;r['after']=r['after_u']/SCALE
    return self.response(rows)
   if p=='/api/products':
    with connect() as db:return self.response([dict(r) for r in db.execute('SELECT * FROM products WHERE deleted=0 ORDER BY id DESC')])
   if p=='/api/receipts':
    with connect() as db:return self.response([receipt(r) for r in db.execute('SELECT * FROM receipts WHERE deleted=0 ORDER BY id DESC LIMIT 500')])
   if p.startswith('/api/receipt-batch/'):
    code=p.rsplit('/',1)[1]
    if len(code)!=13 or not code.isdigit() or ean(code[:12])!=code or not code.startswith('20'):return self.response({'error':'番号が正しくありません。'},400)
    with connect() as db:
     row=db.execute('SELECT batch_id FROM receipts WHERE id=? AND deleted=0',(int(code[2:12])-1,)).fetchone()
     if not row:return self.response({'error':'入庫が見つかりません。'},404)
     if not row['batch_id']:return self.response({'error':'この入庫には一括ラベルがありません。'},400)
     return self.response([receipt(r) for r in db.execute('SELECT * FROM receipts WHERE batch_id=? AND deleted=0 ORDER BY case_seq DESC',(row['batch_id'],))])
   if p.startswith('/api/lookup/'):
    code=p.rsplit('/',1)[1]
    if len(code)!=13 or not code.isdigit() or ean(code[:12])!=code or not code.startswith('20'):return self.response({'error':'このシステムの社内コードではありません。'},400)
    with connect() as db:rows=stock(db,int(code[2:12])-1)
    return self.response(rows[0] if rows else {'error':'この番号は未登録です。'},200 if rows else 404)
   if p.startswith('/api/'):return self.response({'error':'見つかりません。'},404)
   return super().do_GET()
  except Exception as e:
   print(type(e).__name__,file=sys.stderr);return self.response({'error':'データを読み込めません。起動中の画面を確認してください。'},500)
 def do_POST(self):
  if self.headers.get('X-Registry-Token')!=TOKEN:return self.response({'error':'ページを再読み込みしてください。'},403)
  origin=self.headers.get('Origin')
  if origin and urlparse(origin).netloc!=self.headers.get('Host'):return self.response({'error':'送信元が異なります。'},403)
  try:
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=16384:raise ValueError('送信内容が大きすぎるか空です。')
   d=json.loads(self.rfile.read(length))
   if not isinstance(d,dict):raise ValueError('送信内容を確認してください。')
   p=urlparse(self.path).path;now=datetime.now().astimezone().isoformat()
   if p=='/api/inspect':
    with connect() as db:result=jfs.review(db,d,sys.modules[__name__],now)
    return self.response(result,201)
   if p=='/api/staff':
    name=text(d,'name',40,True)
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     if db.execute('SELECT id FROM staff WHERE name=?',(name,)).fetchone():raise ValueError('同じ名前は登録済みです。無効な担当者は「有効にする」で戻せます。同姓同名の場合は部署名などを付けてください。')
     cur=db.execute('INSERT INTO staff(name) VALUES(?)',(name,))
     result=dict(db.execute('SELECT * FROM staff WHERE id=?',(cur.lastrowid,)).fetchone())
    return self.response(result,201)
   if p=='/api/staff-status':
    if type(d.get('active')) is not bool:raise ValueError('状態が正しくありません。')
    with connect() as db:
     cur=db.execute('UPDATE staff SET active=? WHERE id=? AND deleted=0',(int(d['active']),int(d.get('staff_id',0))))
     if cur.rowcount!=1:raise ValueError('担当者が見つかりません。')
    return self.response({'ok':True})
   if p=='/api/units':
    name=text(d,'name',12,True)
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     db.execute('INSERT OR IGNORE INTO units(name,active) VALUES(?,1)',(name,))
     db.execute('UPDATE units SET active=1 WHERE name=?',(name,))
    return self.response({'name':name},201)
   if p=='/api/unit-status':
    if type(d.get('active')) is not bool:raise ValueError('状態を確認してください。')
    with connect() as db:
     cur=db.execute('UPDATE units SET active=? WHERE name=?',(int(d['active']),text(d,'name',12,True)))
     if cur.rowcount!=1:raise ValueError('単位が見つかりません。')
    return self.response({'ok':True})
   if p=='/api/locations':
    name=text(d,'name',40,True)
    with connect() as db:
     db.execute('BEGIN IMMEDIATE');db.execute('INSERT OR IGNORE INTO locations(name,active) VALUES(?,1)',(name,))
     db.execute('UPDATE locations SET active=1 WHERE name=?',(name,))
    return self.response({'name':name},201)
   if p=='/api/location-status':
    with connect() as db:
     cur=db.execute('UPDATE locations SET active=? WHERE name=?',(int(bool(d.get('active'))),text(d,'name',40,True)))
     if cur.rowcount!=1:raise ValueError('保管場所が見つかりません。')
    return self.response({'ok':True})
   if p=='/api/products':
    name=text(d,'name',40,True);spec=text(d,'spec',40);unit=text(d,'unit',12,True);location=text(d,'location',40,True);department=text(d,'department',40);processing=text(d,'processing',20,True);jan=text(d,'jan',13);size=case_size(d.get('case_size',0))
    if processing not in ['加工あり','加工なし','都度判断']:raise ValueError('加工区分を選んでください。')
    if jan and (len(jan)!=13 or not jan.isdigit() or ean(jan[:12])!=jan):raise ValueError('既存JANコードは正しい13桁で入力してください。空欄でも登録できます。')
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     if unit not in active_units(db):raise ValueError('単位設定で登録した単位を選んでください。')
     if location and location not in locations(db):raise ValueError('標準保管場所は「保管場所設定」で登録した場所を選んでください。')
     if db.execute('SELECT id FROM products WHERE name=? AND spec=? AND unit=?',(name,spec,unit)).fetchone():raise ValueError('同じ商品名・規格・単位の商品が登録済みです。')
     staff_id,actor=selected_staff(db,d)
     cur=db.execute('INSERT INTO products(name,spec,unit,location,department,processing,jan,created_at,case_size,staff_id,actor) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(name,spec,unit,location,department,processing,jan,now,size,staff_id,actor))
     row=db.execute('SELECT * FROM products WHERE id=?',(cur.lastrowid,)).fetchone()
    return self.response(dict(row),201)
   if p=='/api/receipts':
    provenance={k:text(d,k,80,k!='delivery_ref') for k in ['supplier','supplier_lot','delivery_ref']}
    manufactured=dates(d,'manufactured')
    received=dates(d,'received');expires=dates(d,'expires') if d.get('expires') else dates({'expires':anniversary(manufactured)},'expires');location=text(d,'location',40,True);key=text(d,'request_id',80,True)
    if expires<received and d.get('entry_kind')!='開始在庫':raise ValueError('賞味期限が入庫日より前です。日付を確認してください。')
    if manufactured>received:raise ValueError('製造日は入庫日以前を選んでください。')
    if manufactured>expires:raise ValueError('賞味期限は製造日以降を選んでください。')
    mode=d.get('quantity_mode','バラ')
    if mode not in ['バラ','ケース']:raise ValueError('入庫単位を選択してください。')
    initial_u=amount(d.get('quantity',0));quantity=initial_u/SCALE
    entry_kind=text(d,'entry_kind',20,True);notes=text(d,'notes',200,False);band=temperature_band(d);total_yen=purchase_yen(d.get('purchase_yen'))
    if entry_kind not in ['入庫','開始在庫']:raise ValueError('入庫区分を選んでください。')
    pid=int(d.get('product_id',0))
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     prior=db.execute('SELECT * FROM receipts WHERE request_id=?',(key,)).fetchone()
     if prior and prior['deleted']:raise ValueError('この入庫は削除済みです。画面を更新してください。')
     if prior:
      if prior['batch_id']:
       return self.response({'receipts':[receipt(r) for r in db.execute('SELECT * FROM receipts WHERE batch_id=? AND deleted=0 ORDER BY case_seq DESC',(prior['batch_id'],))]})
      return self.response(receipt(prior))
     staff_id,actor=selected_staff(db,d)
     if location not in locations(db):raise ValueError('保管場所を登録して選択してください。')
     product=db.execute('SELECT * FROM products WHERE id=? AND deleted=0',(pid,)).fetchone()
     if not product:raise ValueError('登録済みの商品を選んでください。')
     size=product['case_size']
     if mode=='ケース':
      size=case_size(d.get('case_size',size))
      if not size:raise ValueError('商品台帳で1ケースの入数を設定してください。')
      if initial_u%SCALE:raise ValueError('ケース数は整数で入力してください。')
      count=initial_u//SCALE
      if count>200:raise ValueError('一度に発行できるのは200ケースまでです。分けて入庫してください。')
      initial_u=amount(size);quantity=initial_u/SCALE
     else:count=0
     if db.execute('SELECT COALESCE(MAX(id),0) FROM receipts').fetchone()[0]+max(count,1)>9999999998:raise ValueError('管理番号の上限です。')
     rows=[]
     for seq in range(count,0,-1) if count else [0]:
      case_key=key if seq==count else key+':'+str(seq)
      allocated=(total_yen//count+int(count-seq<total_yen%count)) if count and total_yen is not None else total_yen
      cur=db.execute('INSERT INTO receipts(product_id,product_name,spec,unit,received,expires,quantity,location,created_at,request_id,initial_u,actor,entry_kind,staff_id,notes,manufactured,temperature_band,case_size,case_count,batch_id,case_seq,case_total,purchase_yen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,product['name'],product['spec'],product['unit'],received,expires,quantity,location,now,case_key,initial_u,actor,entry_kind,staff_id,notes,manufactured,band,size,1 if count else 0,key if count else '',seq,count,allocated))
      db.execute('UPDATE receipts SET supplier=?,supplier_lot=?,delivery_ref=? WHERE id=?',tuple(provenance.values())+(cur.lastrowid,))
      rows.append(receipt(db.execute('SELECT * FROM receipts WHERE id=?',(cur.lastrowid,)).fetchone()))
    return self.response({'receipts':rows} if count else rows[0],201)
   if p=='/api/delete':
    task=text(d,'task',20,True);key=text(d,'request_id',80,True);reason=text(d,'reason',200,True)
    tables={'product':'products','staff':'staff','receipt':'receipts','movement':'movements'}
    if task not in tables:raise ValueError('削除対象が正しくありません。')
    table=tables[task];target=int(d.get('target_id',0))
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     prior=db.execute('SELECT effect FROM deletions WHERE request_id=?',(key,)).fetchone()
     if prior:return self.response({'ok':True,'effect':prior['effect']})
     row=db.execute('SELECT * FROM '+table+' WHERE id=? AND deleted=0',(target,)).fetchone()
     if not row:raise ValueError('対象は既に削除済みか、存在しません。')
     old=dict(row)
     if d.get('expected')!=old:raise ValueError('情報が変更されています。画面を更新して削除対象を選び直してください。')
     sid,actor=selected_staff(db,d);effect='通常の一覧・選択肢から除外しました。'
     if task=='product':
      if db.execute('SELECT id FROM receipts WHERE product_id=? AND deleted=0',(target,)).fetchone():raise ValueError('入庫記録がある商品は削除できません。入庫・在庫を確認してください。')
     elif task=='staff':
      if old['active'] and db.execute('SELECT COUNT(*) FROM staff WHERE active=1 AND deleted=0').fetchone()[0]<=1:raise ValueError('最後の有効な担当者は削除できません。別の担当者を登録してください。')
      db.execute('UPDATE staff SET active=0 WHERE id=?',(target,))
     elif task=='receipt':
      if db.execute('SELECT id FROM movements WHERE receipt_id=?',(target,)).fetchone():raise ValueError('出庫・棚卸し履歴がある入庫は削除できません。数量の修正は棚卸しで行ってください。')
      effect='入庫を取り消し、在庫から '+str(old['initial_u']/SCALE)+' '+old['unit']+' を除外しました。古いラベルを破棄してください。'
     else:
      if old['kind']=='取消':raise ValueError('削除に伴う取消記録は削除できません。')
      rows=stock(db,old['receipt_id'])
      if not rows:raise ValueError('対象の入庫は削除済みです。')
      current=rows[0]
      if current['revision']!=target:raise ValueError('後続の操作があるため、この履歴は削除できません。数量の修正は棚卸しで行ってください。')
      before=current['balance_u'];after=before-old['delta_u']
      if after<0:raise ValueError('取消後に在庫が不足するため削除できません。')
      db.execute("INSERT INTO movements(receipt_id,kind,delta_u,before_u,after_u,actor,reason,department,created_at,request_id,staff_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(old['receipt_id'],'取消',-old['delta_u'],before,after,actor,'記録ID '+str(target)+' の削除：'+reason,'',now,'delete:'+key,sid))
      jfs.reset(db,old['receipt_id'])
      effect='検品を未確認に戻しました。直近の操作を取り消し、現在庫を '+str(before/SCALE)+' → '+str(after/SCALE)+' '+current['unit']+' に戻しました。'
     db.execute('UPDATE '+table+' SET deleted=1 WHERE id=?',(target,))
     db.execute('INSERT INTO deletions(task,target_id,snapshot,actor,reason,effect,created_at,request_id) VALUES(?,?,?,?,?,?,?,?)',(task,target,json.dumps(old,ensure_ascii=False),actor,reason,effect,now,key))
    return self.response({'ok':True,'effect':effect},201)
   if p=='/api/task-edit':
    task=text(d,'task',20,True);key=text(d,'request_id',80,True);reason=text(d,'edit_reason',200,True)
    tables={'product':'products','staff':'staff','movement':'movements'}
    if task not in tables:raise ValueError('変更対象が正しくありません。')
    table=tables[task];target=int(d.get('target_id',0));values=d.get('values',{})
    if not isinstance(values,dict):raise ValueError('入力内容を確認してください。')
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     if db.execute('SELECT id FROM task_edits WHERE request_id=?',(key,)).fetchone():return self.response({'ok':True})
     row=db.execute('SELECT * FROM '+table+' WHERE id=? AND deleted=0',(target,)).fetchone()
     if not row:raise ValueError('変更対象が見つかりません。')
     old=dict(row)
     if d.get('expected')!=old:raise ValueError('別の操作で情報が変わりました。画面を更新して選び直してください。')
     _,editor=selected_staff(db,d)
     if task=='product':
      after={k:text(values,k,n,k in ['name','unit','location','processing']) for k,n in [('name',40),('spec',40),('unit',12),('location',40),('department',40),('processing',20),('jan',13)]}
      if old['staff_id'] and str(values.get('staff_id',''))==str(old['staff_id']):after.update(staff_id=old['staff_id'],actor=old['actor'])
      else:
       sid,assigned=selected_staff(db,values);after.update(staff_id=sid,actor=assigned)
      after['minimum_u']=amount(values.get('minimum',0),True)
      after['case_size']=case_size(values.get('case_size',old['case_size']))
      if after['unit'] not in active_units(db) and after['unit']!=old['unit']:raise ValueError('単位設定で登録した単位を選んでください。')
      if after['location'] and after['location'] not in locations(db):raise ValueError('標準保管場所は「保管場所設定」で登録した場所を選んでください。')
      if after['processing'] not in ['加工あり','加工なし','都度判断']:raise ValueError('加工区分を選んでください。')
      jan=after['jan']
      if jan and (len(jan)!=13 or not jan.isdigit() or ean(jan[:12])!=jan):raise ValueError('既存JANコードは正しい13桁で入力してください。')
      if after['unit']!=old['unit'] and db.execute('SELECT id FROM receipts WHERE product_id=?',(target,)).fetchone():raise ValueError('入庫済み商品の単位は変更できません。別の商品として登録してください。')
      if db.execute('SELECT id FROM products WHERE name=? AND spec=? AND unit=? AND id!=?',(after['name'],after['spec'],after['unit'],target)).fetchone():raise ValueError('同じ商品名・規格・単位の商品が登録済みです。')
     elif task=='staff':
      after={'name':text(values,'name',40,True)}
      if db.execute('SELECT id FROM staff WHERE name=? AND id!=?',(after['name'],target)).fetchone():raise ValueError('同じ担当者名が登録済みです。')
     else:
      if old['kind']=='取消':raise ValueError('削除に伴う取消記録は変更できません。')
      original=values.get('staff_id')
      if str(original or '')==str(old['staff_id'] or ''):sid,actor=old['staff_id'],old['actor']
      else:sid,actor=selected_staff(db,{'staff_id':original})
      after={'staff_id':sid,'actor':actor,'reason':text(values,'reason',200,True),'department':text(values,'department',40,old['kind'] in ['使用','出荷']),'trace_ref':text(values,'trace_ref',80,old['kind'] in ['使用','出荷'])}
     before={k:old[k] for k in after}
     if before==after:raise ValueError('変更された項目がありません。')
     db.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in after)+' WHERE id=?',tuple(after.values())+(target,))
     db.execute('INSERT INTO task_edits(task,target_id,before_json,after_json,actor,reason,created_at,request_id) VALUES(?,?,?,?,?,?,?,?)',(task,target,json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),editor,reason,now,key))
    return self.response({'ok':True},201)
   if p=='/api/correct':
    key=text(d,'request_id',80,True);reason=text(d,'reason',200,True)
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     prior=db.execute('SELECT * FROM corrections WHERE request_id=?',(key,)).fetchone()
     if prior:
      previous=stock(db,prior['receipt_id'])
      if not previous:raise ValueError('この入庫は削除済みです。')
      return self.response(previous[0])
     rows=stock(db,int(d.get('receipt_id',0)))
     if not rows:raise ValueError('入庫が見つかりません。')
     old=rows[0]
     if int(d.get('edit_revision',-1))!=old['edit_revision'] or int(d.get('revision',-1))!=old['revision']:raise ValueError('別の操作で情報が変わりました。対象を選び直してください。')
     sid,actor=selected_staff(db,d)
     received=dates(d,'received');expires=dates(d,'expires')
     manufactured=dates(d,'manufactured') if d.get('manufactured') else ''
     if old['manufactured'] and not manufactured:raise ValueError('登録済みの製造日は空欄にできません。')
     if manufactured and (manufactured>received or manufactured>expires):raise ValueError('製造日は入庫日・賞味期限以前を選んでください。')
     if text(d,'location',40,True) not in locations(db):raise ValueError('登録済みの保管場所を選んでください。')
     if expires<received and old['entry_kind']!='開始在庫':raise ValueError('賞味期限が入庫日より前です。')
     pid=int(d.get('product_id',0));product=db.execute('SELECT * FROM products WHERE id=? AND deleted=0',(pid,)).fetchone()
     if not product:raise ValueError('商品を選択してください。')
     if pid!=old['product_id'] and old['case_count']:raise ValueError('ケース入庫した商品の変更はできません。入数の違う商品は在庫数に影響するため、入庫記録を作り直してください。')
     if pid!=old['product_id'] and old['revision']:raise ValueError('出庫・棚卸し履歴がある入庫の商品は変更できません。責任者に確認してください。')
     if product['unit']!=old['unit']:raise ValueError('単位の異なる商品へは変更できません。')
     original_id=d.get('original_staff_id','')
     if str(original_id or '')==str(old['staff_id'] or ''):original_id,original_actor=old['staff_id'],old['actor']
     else:original_id,original_actor=selected_staff(db,{'staff_id':original_id})
     keys=['supplier','supplier_lot','delivery_ref','temperature_band','manufactured','product_id','product_name','spec','received','expires','location','notes','staff_id','actor','purchase_yen']
     before={k:old[k] for k in keys}
     after=dict(temperature_band=temperature_band(d,previous=old['temperature_band']),manufactured=manufactured,product_id=pid,product_name=product['name'],spec=product['spec'],received=received,expires=expires,location=text(d,'location',40,True),notes=text(d,'notes',200,False),staff_id=original_id,actor=original_actor,purchase_yen=purchase_yen(d.get('purchase_yen')))
     after.update({k:text(d,k,80,k!='delivery_ref') for k in ['supplier','supplier_lot','delivery_ref']})
     if before==after:raise ValueError('変更された項目がありません。')
     before['quality_status']=old['quality_status'];after['quality_status']='未確認';keys.append('quality_status')
     db.execute('UPDATE receipts SET '+','.join(k+'=?' for k in keys)+',edit_revision=edit_revision+1 WHERE id=?',tuple(after[k] for k in keys)+(old['id'],))
     db.execute('INSERT INTO corrections(receipt_id,before_json,after_json,actor,staff_id,reason,created_at,request_id) VALUES(?,?,?,?,?,?,?,?)',(old['id'],json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),actor,sid,reason,now,key))
     result=stock(db,old['id'])[0]
    return self.response(result,201)
   if p=='/api/minimum':
    minimum=amount(d.get('minimum',0),True);pid=int(d.get('product_id',0))
    with connect() as db:
     cur=db.execute('UPDATE products SET minimum_u=? WHERE id=? AND deleted=0',(minimum,pid))
     if cur.rowcount!=1:raise ValueError('商品が見つかりません。')
    return self.response({'ok':True})
   if p=='/api/move':
    rid=int(d.get('receipt_id',0));kind=text(d,'kind',20,True);reason=text(d,'reason',200,True);department=text(d,'department',40);key=text(d,'request_id',80,True)
    if kind not in ['使用','出荷','廃棄','棚卸し']:raise ValueError('処理区分を選んでください。')
    trace_ref=text(d,'trace_ref',80,kind in ['使用','出荷'])
    if kind in ['使用','出荷'] and not department:raise ValueError('使用部署または出荷先を入力してください。')
    qty=amount(d.get('quantity',0),kind=='棚卸し');entered=qty
    mode=d.get('quantity_mode','バラ')
    if mode not in ['バラ','ケース']:raise ValueError('数量の単位を選んでください。')
    loose=amount(d.get('loose_quantity',0),True)
    if loose and (kind!='棚卸し' or mode!='ケース'):raise ValueError('バラ残数はケース単位の棚卸しで入力してください。')
    with connect() as db:
     db.execute('BEGIN IMMEDIATE')
     prior=db.execute('SELECT * FROM movements WHERE request_id=?',(key,)).fetchone()
     if prior and prior['deleted']:raise ValueError('この操作は削除済みです。画面を更新してください。')
     if prior:return self.response({'movement':dict(prior),'stock':stock(db,prior['receipt_id'])[0]})
     staff_id,actor=selected_staff(db,d)
     rows=stock(db,rid)
     if not rows:raise ValueError('入庫情報が見つかりません。')
     row=rows[0];before=row['balance_u']
     if int(d.get('edit_revision',-1))!=row['edit_revision']:raise ValueError('入庫情報が訂正されました。対象を選び直してください。')
     if int(d.get('revision',-1))!=row['revision']:raise ValueError('別の操作で在庫が変わりました。対象を読み直して数量を確認してください。')
     if kind in ['使用','出荷'] and row['quality_status']!='合格':raise ValueError('検品が合格ではありません。JFS検品画面で判定してください。')
     if kind in ['使用','出荷'] and row['days_left']<0:raise ValueError('賞味期限切れのため、使用・出荷は登録できません。')
     size=row['case_size'] if mode=='ケース' else 0
     if mode=='ケース':
      if not size or not row['case_count']:raise ValueError('ケース入庫した在庫を選んでください。')
      if qty%SCALE:raise ValueError('ケース数は整数で入力してください。')
      if loose>=size*SCALE:raise ValueError('バラ残数は1ケースの入数未満で入力してください。')
      qty=qty*size+loose
      if qty>1000000000*SCALE:raise ValueError('換算後の数量が上限を超えています。')
     delta=qty-before if kind=='棚卸し' else -qty
     after=before+delta
     if kind=='棚卸し' and row['case_seq'] and after>row['case_size']*SCALE:raise ValueError('このラベルは1ケース分です。棚卸しは1ケースの入数以下で入力してください。')
     if after<0:raise ValueError('在庫が不足しています。現在庫を超える出庫はできません。')
     cur=db.execute('INSERT INTO movements(receipt_id,kind,delta_u,before_u,after_u,actor,reason,department,created_at,request_id,staff_id,quantity_mode,input_u,input_loose_u,case_size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,kind,delta,before,after,actor,reason,department,now,key,staff_id,mode,entered,loose,size))
     db.execute('UPDATE movements SET trace_ref=? WHERE id=?',(trace_ref,cur.lastrowid))
     if kind=='棚卸し' and delta:jfs.reset(db,rid)
     movement=dict(db.execute('SELECT * FROM movements WHERE id=?',(cur.lastrowid,)).fetchone())
     result=stock(db,rid)[0]
    return self.response({'movement':movement,'stock':result},201)
   return self.response({'error':'見つかりません。'},404)
  except (ValueError,TypeError,OverflowError,json.JSONDecodeError) as e:return self.response({'error':str(e)},400)
  except Exception as e:
   print(type(e).__name__,file=sys.stderr);return self.response({'error':'保存できませんでした。入力は残っています。再度お試しください。'},500)
if __name__=='__main__':
 try:server=ThreadingHTTPServer((HOST,PORT),Handler)
 except OSError:
  print('起動できません。以前のアプリをControl+Cで終了してから、もう一度起動してください。');sys.exit(1)
 try:init()
 except Exception as e:
  server.server_close();print('データの準備に失敗しました。元データは削除せず、表示を担当者にお知らせください。',str(e));sys.exit(1)
 url=f'http://127.0.0.1:{PORT}'
 print('\n在庫・入庫ラベル管理 V3.0.2 JFS-B運用支援を起動しました。\n'+url+'\n終了：Ctrl+C\nデータ保存先：'+str(DB),flush=True)
 if '--no-browser' not in sys.argv:threading.Timer(.6,lambda:webbrowser.open(url)).start()
 try:server.serve_forever()
 except KeyboardInterrupt:server.server_close()
