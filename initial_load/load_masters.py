# =============================================================
# load_masters.py — 部署マスタと社員マスタを Supabase に入れる（v5用）
#
# 使い方（initial_load フォルダで実行）：
#   python load_masters.py --dry-run   読むだけ。DBには書き込まない（最初はこちら）
#   python load_masters.py             DBに書き込む
#
# 前提：
#   ・SQL/v5_create.sql を実行済み
#   ・initial_load/data/ に db_load_673.json を置く（部署と社員もこのJSONから読む）
#   ・リポジトリ直下の .env に SUPABASE_URL と SUPABASE_KEY を書く
#   ・同じ番号（dept_code、emp_no）があれば上書き（upsert）するので、何度実行しても重複しない
# =============================================================
import sys

import loadlib as lib

DEPT = [("dept_code", "dept_id"), ("dept_name", None), ("dept_type", None)]
# 社員の dept_code は、DBに入れる前に bigint の dept_id に置き換える
EMP = [("emp_no", None), ("name", None), ("dept_code", "dept_id"),
       ("site", None), ("title", None), ("is_active", None)]


def main():
    dry_run = "--dry-run" in sys.argv
    data = lib.load_json()

    depts, p1, d1 = lib.read_rows(data, "departments", DEPT, ["dept_code", "dept_name", "dept_type"], ["dept_code"])
    emps, p2, d2 = lib.read_rows(data, "employees", EMP, ["emp_no", "name", "dept_code"], ["emp_no"])
    problems = []
    for label, rows, p, dropped in (("departments", depts, p1, d1), ("employees", emps, p2, d2)):
        print(f"【{label}】{len(rows)}件")
        if dropped:
            print(f"   DBにない項目は入れません：{', '.join(dropped)}")
        problems += p

    unknown = lib.unmapped(emps, "dept_code", {r["dept_code"] for r in depts})
    if unknown:
        problems.append(f"部署にない部署番号を持つ社員がいます：{', '.join(unknown)}")
    for p in problems:
        print(f"   × {p}")
    if problems:
        sys.exit("\n問題があるため、DBには書き込みませんでした。")
    if dry_run:
        print("\n--dry-run のため、ここで終了します（DBには書き込んでいません）。")
        return

    sb = lib.connect()
    lib.upsert(sb, "departments", depts, "dept_code")          # 部署 → 社員の順
    dept_map = lib.fetch_map(sb, "departments", "dept_code", "dept_id")
    for r in emps:
        r["dept_id"] = dept_map[r.pop("dept_code")]
    lib.upsert(sb, "employees", emps, "emp_no")


if __name__ == "__main__":
    main()
