# =============================================================
# load_documents.py — 文書・章・著者の3つの棚を Supabase に入れる
#
# 使い方（initial_load フォルダで実行）：
#   python load_documents.py --dry-run   読むだけ。DBには書き込まない（最初はこちら）
#   python load_documents.py             DBに書き込む
#
# 前提：
#   ・部署マスタと社員マスタは load_masters.py で入れ済み
#   ・initial_load/data/ に db_load_373.json を置く
#   ・data-triangle フォルダ直下の .env に SUPABASE_URL と SUPABASE_KEY を書く
#   ・同じキーがすでにあれば上書き（upsert）するので、何度実行しても重複しない
#   ・章の embedding は入れない（空のまま）。埋め込みは別のコードで書き込む
# =============================================================
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
DATA_FILE = HERE / "data" / "db_load_373.json"
MASTER_DIR = HERE / "masters"

# DBの棚にある列だけを取り出す（順番が大事：文書 → 章 → 著者）
TABLES = [
    # (棚の名前, 列, 必須の列, 重複を見分けるキー)
    ("documents",
     ["doc_id", "doc_type", "doc_category", "title", "source_file",
      "submitted_at", "proposal_area", "result", "decided_at", "result_reason",
      "pj_status", "started_at", "planned_end_at", "ended_at",
      "owner_dept_id", "updated_at"],
     ["doc_id", "doc_type", "doc_category", "title", "source_file"],
     ["doc_id"]),
    ("document_sections",
     ["doc_id", "section_no", "section_name", "section_role", "body"],
     ["doc_id", "section_no", "section_name", "section_role", "body"],
     ["doc_id", "section_no"]),
    ("document_authors",
     ["doc_id", "emp_no", "role", "dept_id_at_time"],
     ["doc_id", "emp_no", "role", "dept_id_at_time"],
     ["doc_id", "emp_no"]),
]
CHUNK = 200  # 一度に送る行数


def clean(value):
    """前後の空白を取り、空文字は NULL（None）にする。数値（章の番号）はそのまま"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return text or None


def read_rows(data, table, columns, required, key):
    if table not in data:
        sys.exit(f"JSONに {table} がありません：{DATA_FILE}")
    items = data[table]

    rows, problems = [], []
    for i, item in enumerate(items, start=1):
        row = {c: clean(item.get(c)) for c in columns}
        empty = [c for c in required if row[c] is None]
        if empty:
            problems.append(f"{i}件目：必須の列が空（{', '.join(empty)}）")
        rows.append(row)

    keys = [tuple(r[c] for c in key) for r in rows]
    seen, dup = set(), set()
    for k in keys:
        (dup if k in seen else seen).add(k)
    if dup:
        problems.append(f"キーの重複：{', '.join('/'.join(map(str, k)) for k in sorted(dup)[:10])}")

    dropped = sorted({k for item in items for k in item} - set(columns))
    return rows, problems, dropped


def load_ids(filename, column):
    path = MASTER_DIR / filename
    if not path.exists():
        return None
    return {str(x[column]).strip() for x in json.loads(path.read_text(encoding="utf-8"))}


def check_links(prepared):
    """外部キーの事前チェック。マスタのJSONが手元にあれば、それとも突き合わせる"""
    rows = dict(prepared)
    problems = []

    doc_ids = {r["doc_id"] for r in rows["documents"]}
    for table in ("document_sections", "document_authors"):
        orphan = sorted({r["doc_id"] for r in rows[table]} - doc_ids)
        if orphan:
            problems.append(f"{table}：文書にない文書ID：{', '.join(orphan[:10])}")
    no_section = sorted(doc_ids - {r["doc_id"] for r in rows["document_sections"]})
    if no_section:
        problems.append(f"章が1つもない文書：{', '.join(no_section[:10])}")

    dept_ids = load_ids("departments.json", "dept_id")
    emp_nos = load_ids("employees.json", "emp_no")
    if dept_ids is None or emp_nos is None:
        print("   （masters/ にマスタのJSONがないため、部署と社員の突き合わせは省きます）")
        return problems

    used_depts = ({r["owner_dept_id"] for r in rows["documents"] if r["owner_dept_id"]}
                  | {r["dept_id_at_time"] for r in rows["document_authors"]})
    unknown = sorted(used_depts - dept_ids)
    if unknown:
        problems.append(f"部署マスタにない部署ID：{', '.join(unknown)}")
    unknown = sorted({r["emp_no"] for r in rows["document_authors"]} - emp_nos)
    if unknown:
        problems.append(f"社員マスタにない社員番号：{', '.join(unknown[:10])}")
    return problems


def main():
    dry_run = "--dry-run" in sys.argv

    # ---------- 読む・整える ----------
    if not DATA_FILE.exists():
        sys.exit(f"ファイルが見つかりません：{DATA_FILE}")
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    prepared = []
    has_problem = False
    for table, columns, required, key in TABLES:
        rows, problems, dropped = read_rows(data, table, columns, required, key)
        print(f"【{table}】{len(rows)}件")
        if dropped:
            print(f"   DBにない項目は入れません：{', '.join(dropped)}")
        for p in problems:
            print(f"   × {p}")
        has_problem |= bool(problems)
        prepared.append((table, rows))

    for p in check_links(prepared):
        print(f"   × {p}")
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

    for (table, rows), (_, _, _, conflict) in zip(prepared, TABLES):  # 文書 → 章 → 著者の順
        try:
            for start in range(0, len(rows), CHUNK):
                sb.table(table).upsert(rows[start:start + CHUNK],
                                       on_conflict=",".join(conflict)).execute()
        except Exception as e:
            if "42P10" in str(e):  # upsert のキーに一意制約がない
                sys.exit(f"\n【{table}】({', '.join(conflict)}) に一意制約がないため、上書きできません。\n"
                         f"SQLエディタで次を実行してから、やり直してください：\n"
                         f"  alter table {table} add unique ({', '.join(conflict)});")
            raise
        count = sb.table(table).select("*", count="exact", head=True).execute().count
        print(f"【{table}】書き込み完了。DBの件数：{count}件")


if __name__ == "__main__":
    main()
