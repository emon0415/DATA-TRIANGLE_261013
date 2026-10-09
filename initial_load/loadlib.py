# =============================================================
# loadlib.py — load_masters.py と load_documents.py の共通部品（v5用）
#
# v5では主キーが bigint の自動採番になった。JSON（db_load_673.json）の文字列のID
# （dept_id / doc_id など）は、DBでは *_code 列に入れる。子の行は、DBに入った親の
# bigint を引いて参照する。
# JSONのキー名は、旧名（dept_id、doc_id）でも新名（dept_code、doc_code）でも読める。
# =============================================================
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
DATA_FILE = HERE / "data" / "db_load_673.json"
CHUNK = 200   # 一度に送る行数
PAGE = 1000   # DBから一度に読む行数


def load_json():
    """読むJSON。コマンドの引数（--で始まらないもの）にファイルを書けば、そのJSONを読む。なければ db_load_673.json"""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0]) if args else DATA_FILE
    if not path.exists():
        sys.exit(f"ファイルが見つかりません：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def clean(value):
    """前後の空白を取り、空文字は NULL（None）にする。数値と真偽値はそのまま"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value).strip()
    return text or None


def read_rows(data, table, spec, required, key):
    """spec: [(DBの列, JSONの旧キー名 or None), ...]。JSONは新名・旧名のどちらでも読む"""
    if table not in data:
        sys.exit(f"JSONに {table} がありません：{DATA_FILE}")
    items = data[table]
    rows, problems = [], []
    for i, item in enumerate(items, start=1):
        row = {}
        for col, old in spec:
            row[col] = clean(item[col] if col in item else item.get(old) if old else None)
        empty = [c for c in required if row[c] is None]
        if empty:
            problems.append(f"{i}件目：必須の列が空（{', '.join(empty)}）")
        rows.append(row)

    seen, dup = set(), set()
    for k in (tuple(r[c] for c in key) for r in rows):
        (dup if k in seen else seen).add(k)
    if dup:
        problems.append(f"キーの重複：{', '.join('/'.join(map(str, k)) for k in sorted(dup)[:10])}")

    known = {c for c, _ in spec} | {o for _, o in spec if o}
    dropped = sorted({k for item in items for k in item} - known)
    return rows, problems, dropped


def connect():
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(HERE.parent / ".env")
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        sys.exit(".env に SUPABASE_URL と SUPABASE_KEY が見つかりません。")
    return create_client(url, key)


def upsert(sb, table, rows, conflict):
    for start in range(0, len(rows), CHUNK):
        sb.table(table).upsert(rows[start:start + CHUNK], on_conflict=conflict).execute()
    count = sb.table(table).select("*", count="exact", head=True).execute().count
    print(f"【{table}】書き込み完了。DBの件数：{count}件")


def fetch_map(sb, table, code_col, id_col):
    """DBにある親の「番号 → bigint」の対応表を、すべて読む"""
    out, start = {}, 0
    while True:
        res = (sb.table(table).select(f"{id_col},{code_col}").order(id_col)
               .range(start, start + PAGE - 1).execute())
        out.update({r[code_col]: r[id_col] for r in res.data})
        if len(res.data) < PAGE:
            return out
        start += PAGE


def unmapped(rows, col, mapping):
    return sorted({r[col] for r in rows if r[col] is not None and r[col] not in mapping})
