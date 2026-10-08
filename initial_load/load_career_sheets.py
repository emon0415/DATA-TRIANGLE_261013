# =============================================================
# load_career_sheets.py — キャリアシート（本人が書いた3項目）を profile_notes に入れる
#
# キャリアシートの項目（各1000字まで。書かない項目は空でよい）：
#   現在の職務／将来やりたいこと／そのために取り組んでいること
# 全社員の2〜3割が出している想定。出していない人は、入れないだけ（看板は文書のノートだけで作る）。
#
# 使い方（リポジトリ直下で実行）：
#   python initial_load/load_career_sheets.py --dry-run   中身の検査だけ。DBには書かない
#   python initial_load/load_career_sheets.py             DBに書く（同じ人・同じ項目は上書き）
#   python initial_load/load_career_sheets.py ファイル.json   別のJSONを読む
# そのあと、看板を作り直す：python initial_load/build_profiles.py
#
# 前提：
#   ・SQL/v5_profile_notes.sql を SQL Editor で実行済み（memo_kind の列がある）
#   ・社員が入っている（load_masters.py）
#
# JSONの形（initial_load/data/career_sheets_mock.json）：
#   {"sheets": [{"emp_no": "2346", "updated_at": "2026-09-01",
#                "現在の職務": "…", "将来やりたいこと": "…", "そのために取り組んでいること": "…"}, ...]}
# =============================================================
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_FILE = HERE / "data" / "career_sheets_mock.json"
KINDS = ["現在の職務", "将来やりたいこと", "そのために取り組んでいること"]
MAX_CHARS = 1000


def validate(sheets, emp_id_of):
    """検査して、DBに書く行と、問題の一覧を返す。DBには触れない。
    emp_id_of：{社員番号: 社員ID}"""
    rows, problems, seen = [], [], set()
    for i, sh in enumerate(sheets, start=1):
        no = str(sh.get("emp_no", "")).strip()
        if no not in emp_id_of:
            problems.append(f"{i}件目：社員番号 {no!r} が社員にありません")
            continue
        if no in seen:
            problems.append(f"{i}件目：社員番号 {no} が重複しています")
            continue
        seen.add(no)
        unknown = sorted(set(sh) - {"emp_no", "updated_at", *KINDS})
        if unknown:
            problems.append(f"{i}件目（{no}）：知らない項目 {unknown}")
        n_items = 0
        for kind in KINDS:
            body = (sh.get(kind) or "").strip()
            if not body:
                continue
            if len(body) > MAX_CHARS:
                problems.append(f"{i}件目（{no}）：{kind} が{len(body)}字（上限{MAX_CHARS}字）")
                continue
            n_items += 1
            row = {"emp_id": emp_id_of[no], "doc_id": None, "body": body, "body_source": "本人入力", "memo_kind": kind}
            if sh.get("updated_at"):
                row["updated_at"] = str(sh["updated_at"])
            rows.append(row)
        if n_items == 0:
            problems.append(f"{i}件目（{no}）：3項目がすべて空です")
    return rows, problems


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0]) if args else DEFAULT_FILE
    if not path.exists():
        sys.exit(f"ファイルが見つかりません：{path}")
    sheets = json.loads(path.read_text(encoding="utf-8"))["sheets"]

    sys.path.insert(0, str(HERE.parent))
    from register import db
    sb = db.connect()
    emps = db.fetch_all(sb, "employees", "emp_id,emp_no")
    rows, problems = validate(sheets, {e["emp_no"]: e["emp_id"] for e in emps})

    people = len({r["emp_id"] for r in rows})
    lens = sorted(len(r["body"]) for r in rows)
    print(f"キャリアシート：{len(sheets)}人分を読みました（書ける人 {people}人／ノート {len(rows)}枚）")
    if lens:
        print(f"本文の長さ：中央 {lens[len(lens) // 2]}字／最大 {lens[-1]}字（上限 {MAX_CHARS}字）")
    if problems:
        print("\n問題があります：")
        for p in problems:
            print("  ・" + p)
        sys.exit("直してから、もう一度実行してください（DBには何も書いていません）。")
    if "--dry-run" in sys.argv:
        print("\n--dry-run のため、ここで終了します（DBには書いていません）。")
        return
    for start in range(0, len(rows), 200):
        sb.table("profile_notes").upsert(rows[start:start + 200], on_conflict="emp_id,memo_kind").execute()
    print(f"\n書き込み完了。次に、看板を作り直してください：python initial_load/build_profiles.py")


if __name__ == "__main__":
    main()
