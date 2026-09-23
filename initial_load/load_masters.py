# =============================================================
# load_masters.py — 部署マスタと社員マスタを Supabase に入れる
#
# 使い方（_test フォルダで実行）：
#   python load_masters.py --dry-run   読むだけ。DBには書き込まない（最初はこちら）
#   python load_masters.py             DBに書き込む
#
# 前提：
#   ・_test/masters/ に departments.json と employees.json を置く
#   ・data-triangle フォルダ直下の .env に SUPABASE_URL と SUPABASE_KEY を書く
#   ・同じIDがすでにあれば上書き（upsert）するので、何度実行しても重複しない
# =============================================================
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
MASTER_DIR = HERE / "masters"

# DBの棚にある列だけを取り出す（JSONの tier などDBにない項目は捨てる）
TABLES = [
    # (棚の名前, JSONファイル, 列, 必須の列)
    ("departments", "departments.json",
     ["dept_id", "dept_name", "dept_type"],
     ["dept_id", "dept_name", "dept_type"]),
    ("employees", "employees.json",
     ["emp_no", "name", "dept_id", "site", "title"],
     ["emp_no", "name", "dept_id"]),
]
CHUNK = 200  # 一度に送る行数


def clean(value):
    """前後の空白を取り、空文字は NULL（None）にする。社員番号などは文字列にそろえる"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def read_rows(filename, columns, required):
    path = MASTER_DIR / filename
    if not path.exists():
        sys.exit(f"ファイルが見つかりません：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))

    rows, problems = [], []
    for i, item in enumerate(data, start=1):
        row = {c: clean(item.get(c)) for c in columns}
        empty = [c for c in required if row[c] is None]
        if empty:
            problems.append(f"{i}件目：必須の列が空（{', '.join(empty)}）")
        rows.append(row)

    ids = [r[columns[0]] for r in rows]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        problems.append(f"IDの重複：{', '.join(dup)}")

    dropped = sorted({k for item in data for k in item} - set(columns))
    return rows, problems, dropped


def main():
    dry_run = "--dry-run" in sys.argv

    # ---------- 読む・整える ----------
    prepared = []
    has_problem = False
    for table, filename, columns, required in TABLES:
        rows, problems, dropped = read_rows(filename, columns, required)
        print(f"【{table}】{filename}：{len(rows)}件")
        if dropped:
            print(f"   DBにない項目は入れません：{', '.join(dropped)}")
        for p in problems:
            print(f"   × {p}")
        has_problem |= bool(problems)
        prepared.append((table, rows))

    # 社員の部署が、部署マスタにあるか（外部キーの事前チェック）
    dept_ids = {r["dept_id"] for r in prepared[0][1]}
    unknown = sorted({r["dept_id"] for r in prepared[1][1]} - dept_ids)
    if unknown:
        print(f"   × 部署マスタにない部署IDを持つ社員がいます：{', '.join(unknown)}")
        has_problem = True

    if has_problem:
        sys.exit("\n問題があるため、DBには書き込みませんでした。")
    if dry_run:
        print("\n--dry-run のため、ここで終了します（DBには書き込んでいません）。")
        return

    # ---------- 入れる ----------
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(HERE.parent / ".env")
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        sys.exit(".env に SUPABASE_URL と SUPABASE_KEY が見つかりません。")
    sb = create_client(url, key)

    for table, rows in prepared:  # 部署 → 社員の順（順番が大事）
        for start in range(0, len(rows), CHUNK):
            sb.table(table).upsert(rows[start:start + CHUNK]).execute()
        count = sb.table(table).select("*", count="exact", head=True).execute().count
        print(f"【{table}】書き込み完了。DBの件数：{count}件")


if __name__ == "__main__":
    main()