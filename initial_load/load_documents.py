# =============================================================
# load_documents.py — 文書・章・著者・キーワードを Supabase に入れる（v5用）
#
# 使い方（initial_load フォルダで実行）：
#   python load_documents.py --dry-run   読むだけ。DBには書き込まない（最初はこちら）
#   python load_documents.py             DBに書き込む
#
# 前提：
#   ・load_masters.py で部署と社員を入れ済み
#   ・initial_load/data/ に db_load_673.json を置く
#   ・同じキー（doc_code、章は doc_id＋section_no など）があれば上書きする
#   ・章の embedding は入れない（空のまま）。embed_sections.py で書き込む
#   ・body_tokens と document_keywords はJSONの値をそのまま入れる
# =============================================================
import sys

import loadlib as lib

DOCS = [("doc_code", "doc_id"), ("doc_type", None), ("doc_category", None), ("title", None),
        ("source_file", None), ("submitted_at", None), ("proposal_area", None), ("result", None),
        ("decided_at", None), ("result_reason", None), ("pj_status", None), ("started_at", None),
        ("planned_end_at", None), ("ended_at", None), ("owner_dept_code", "owner_dept_id"),
        ("updated_at", None)]
SECS = [("doc_code", "doc_id"), ("section_no", None), ("section_name", None),
        ("section_role", None), ("body", None), ("body_tokens", None)]
AUTH = [("doc_code", "doc_id"), ("emp_no", None), ("role", None), ("dept_code", "dept_id_at_time")]
KEYW = [("doc_code", "doc_id"), ("keyword", None), ("count", None)]

TABLES = [  # (棚, spec, 必須, キー)　順番が大事：文書 → 章・著者・キーワード
    ("documents", DOCS, ["doc_code", "doc_type", "doc_category", "title", "source_file"], ["doc_code"]),
    ("document_sections", SECS, ["doc_code", "section_no", "section_name", "section_role", "body"],
     ["doc_code", "section_no"]),
    ("document_authors", AUTH, ["doc_code", "emp_no", "role", "dept_code"], ["doc_code", "emp_no"]),
    ("document_keywords", KEYW, ["doc_code", "keyword", "count"], ["doc_code", "keyword"]),
]


def main():
    dry_run = "--dry-run" in sys.argv
    data = lib.load_json()

    prepared, problems = {}, []
    for table, spec, required, key in TABLES:
        rows, p, dropped = lib.read_rows(data, table, spec, required, key)
        print(f"【{table}】{len(rows)}件")
        if dropped:
            print(f"   DBにない項目は入れません：{', '.join(dropped)}")
        problems += p
        prepared[table] = rows

    # JSONの中の突き合わせ（部署・社員もこのJSONにある）
    dept_codes = {r["dept_id"] if "dept_id" in r else r["dept_code"] for r in data["departments"]}
    emp_nos = {str(r["emp_no"]).strip() for r in data["employees"]}
    doc_codes = {r["doc_code"] for r in prepared["documents"]}
    for table in ("document_sections", "document_authors", "document_keywords"):
        orphan = lib.unmapped(prepared[table], "doc_code", doc_codes)
        if orphan:
            problems.append(f"{table}：文書にない文書番号：{', '.join(orphan[:10])}")
    no_sec = sorted(doc_codes - {r["doc_code"] for r in prepared["document_sections"]})
    if no_sec:
        problems.append(f"章が1つもない文書：{', '.join(no_sec[:10])}")
    unknown = sorted(({r["owner_dept_code"] for r in prepared["documents"] if r["owner_dept_code"]}
                      | {r["dept_code"] for r in prepared["document_authors"]}) - dept_codes)
    if unknown:
        problems.append(f"部署にない部署番号：{', '.join(unknown)}")
    unknown = lib.unmapped(prepared["document_authors"], "emp_no", emp_nos)
    if unknown:
        problems.append(f"社員にない社員番号：{', '.join(unknown[:10])}")

    for p in problems:
        print(f"   × {p}")
    if problems:
        sys.exit("\n問題があるため、DBには書き込みませんでした。")
    if dry_run:
        print("\n--dry-run のため、ここで終了します（DBには書き込んでいません）。")
        return

    sb = lib.connect()
    dept_map = lib.fetch_map(sb, "departments", "dept_code", "dept_id")
    emp_map = lib.fetch_map(sb, "employees", "emp_no", "emp_id")
    bad = lib.unmapped(prepared["document_authors"], "emp_no", emp_map)
    bad_d = lib.unmapped(prepared["document_authors"], "dept_code", dept_map)
    if bad or bad_d:
        sys.exit(f"DBに部署・社員がありません。先に load_masters.py を実行してください。{(bad + bad_d)[:5]}")

    docs = prepared["documents"]                       # 文書：owner_dept を bigint に
    for r in docs:
        code = r.pop("owner_dept_code")
        r["owner_dept_id"] = dept_map[code] if code else None
    lib.upsert(sb, "documents", docs, "doc_code")
    doc_map = lib.fetch_map(sb, "documents", "doc_code", "doc_id")

    for r in prepared["document_authors"]:             # 著者：部署と社員も bigint に
        r["emp_id"] = emp_map[r.pop("emp_no")]
        r["dept_id_at_time"] = dept_map[r.pop("dept_code")]
    conflicts = {"document_sections": "doc_id,section_no",
                 "document_authors": "doc_id,emp_id",
                 "document_keywords": "doc_id,keyword"}
    for table, conflict in conflicts.items():          # 子：doc_code → doc_id
        rows = prepared[table]
        for r in rows:
            r["doc_id"] = doc_map[r.pop("doc_code")]
        lib.upsert(sb, table, rows, conflict)


if __name__ == "__main__":
    main()
