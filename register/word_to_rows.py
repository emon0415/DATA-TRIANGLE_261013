# =============================================================
# word_to_rows.py — Word文書（.docx）を、文書・章・著者の3つの棚の行に変えて登録する
#
# 使い方（data-triangle フォルダで実行）：
#   python register/word_to_rows.py ファイル.docx [ファイル.docx ...]
#   → 読むだけ。DBには書き込まない（お試し実行。マスタと登録済みの文書はDBから読む）
#   python register/word_to_rows.py --save ファイル.docx ...
#   → 問題がなければDBに書き込む（登録済みの文書を上書きするときは --overwrite も付ける）
#
# 画面（登録タブ）からは read_word(ファイルの中身, ファイル名, db.load_masters(sb)) を呼ぶ。
#
# 前提：
#   ・Wordは次の形であること
#       表題        … スタイル「表題（Title）」の段落
#       文書情報    … 1つ目の表（左に項目名、右に値）
#       提案者・体制 … 2つ目の表（役割｜社員番号｜氏名｜部署）
#       章          … スタイル「見出し1（Heading 1）」の段落で区切る
#   ・部署名と社員番号の確認には、DBの departments と employees を使う
#   ・DBの接続情報は data-triangle フォルダ直下の .env に書く（SUPABASE_URL、SUPABASE_KEY）
#   ・章の embedding は入れない（空のまま）。埋め込みは別の処理で書き込む
#   ・python-docx は使わず、標準ライブラリだけで読む
# =============================================================
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W ="{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_BYTES = 10 * 1024 * 1024  # 10MBより大きいファイルは受け付けない

# 文書の種類（Wordの「文書の種類」）→ doc_type、doc_category、文書IDの頭文字
DOC_TYPES = {
    "提案書": {"doc_type": "proposal", "doc_category": "1-4", "prefix": "KZ-"},
    "PJ文書": {"doc_type": "project", "doc_category": "2-1", "prefix": "PJ-"},
}

# 文書情報の表の項目名 → documents の列（種類ごと）
INFO_LABELS = {
    "proposal": {"提出日": "submitted_at", "提案区分": "proposal_area", "判定": "result",
                 "判定日": "decided_at", "判定理由": "result_reason"},
    "project": {"状態": "pj_status", "開始日": "started_at", "終了予定日": "planned_end_at",
                "終了日": "ended_at", "主管部門": "owner_dept_id", "最終更新日": "updated_at"},
}
REQUIRED = {"proposal": ["submitted_at", "proposal_area", "result"],
            "project": ["pj_status", "started_at", "owner_dept_id"]}
DATE_COLUMNS = {"submitted_at", "decided_at", "started_at", "planned_end_at", "ended_at", "updated_at"}

# 値を今のDBにある値に限る
ALLOWED = {
    "proposal_area": {"業務効率", "コスト", "納期・スピード", "安全・環境", "品質", "その他"},
    "result": {"採択", "不採択", "保留", "審査中"},
    "pj_status": {"完了", "進行中", "中止", "計画中"},
}
AUTHOR_ROLES = {"proposal": {"提案者"}, "project": {"責任者", "主担当", "副担当"}}

# 章の名前 → 章の役割（今のDBと同じ対応）
SECTION_ROLES = {
    "proposal": {"現状": "背景", "問題点": "課題意識", "提案内容": "行動案",
                 "期待効果": "目標", "実施上の課題": "実施上の課題"},
    "project": {"背景": "背景", "目的": "目的", "実施内容": "行動", "成果": "成果"},
}


def text_of(element):
    return "".join(t.text or "" for t in element.iter(W + "t")).strip()


def style_names(z):
    """スタイルID → スタイル名（日本語のWordではIDが "1" などになるため、名前で判定する）"""
    if "word/styles.xml" not in z.namelist():
        return {}
    root = ET.fromstring(z.read("word/styles.xml"))
    names = {}
    for s in root.iter(W + "style"):
        name = s.find(W + "name")
        names[s.get(W + "styleId")] = (name.get(W + "val") if name is not None else "").lower()
    return names


def read_blocks(data):
    """Wordの本文を、上から順に ("title"|"heading"|"text", 文字) と ("table", 行のリスト) に分ける"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = style_names(z)
        body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
    blocks = []
    for el in body:
        if el.tag == W + "p":
            text = text_of(el)
            if not text:
                continue
            ps = el.find(f"{W}pPr/{W}pStyle")
            style = names.get(ps.get(W + "val"), "") if ps is not None else ""
            kind = "title" if style == "title" else "heading" if style == "heading 1" else "text"
            blocks.append((kind, text))
        elif el.tag == W + "tbl":
            rows = [[text_of(c) for c in r.iter(W + "tc")] for r in el.iter(W + "tr")]
            blocks.append(("table", rows))
    return blocks


def read_word(data, filename, masters):
    """Wordの中身（bytes）から3つの棚の行を作る。
    戻り値：{"documents": [...], "document_sections": [...], "document_authors": [...]},
            問題（登録を止める）のリスト, 注意（登録はできる）のリスト"""
    problems, notes = [], []
    dept_ids, employees = masters
    empty = {"documents": [], "document_sections": [], "document_authors": []}

    if not filename.lower().endswith(".docx"):
        return empty, ["Word文書（.docx）ではありません"], notes
    if len(data) > MAX_BYTES:
        return empty, [f"ファイルが大きすぎます（上限 {MAX_BYTES // 1024 // 1024}MB）"], notes
    try:
        blocks = read_blocks(data)
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return empty, ["Word文書として読めません（壊れているか、.docx ではありません）"], notes

    tables = [b[1] for b in blocks if b[0] == "table"]
    titles = [b[1] for b in blocks if b[0] == "title"]
    if len(tables) < 2:
        return empty, ["「文書情報」と「提案者（体制）」の2つの表が見つかりません"], notes

    # ---------- 文書情報 → documents ----------
    info = {r[0]: r[1].strip() for r in tables[0] if len(r) >= 2}
    if "文書の種類" not in info:
        return empty, ["ひな形と形が違います。1つ目の表（文書情報）に「文書の種類」の行がありません"
                       "（以前の書式のWordは読めません）"], notes
    kind = DOC_TYPES.get(info["文書の種類"])
    if kind is None:
        return empty, [f"文書の種類「{info.get('文書の種類', '')}」には対応していません"
                       f"（対応：{'、'.join(DOC_TYPES)}）"], notes
    doc_type = kind["doc_type"]
    doc_id = info.get("文書ID", "")
    if not re.fullmatch(re.escape(kind["prefix"]) + r"\d{4}-\d{4}", doc_id):
        problems.append(f"文書ID「{doc_id}」の形が違います（例：{kind['prefix']}2026-0001）")
    if Path(filename).stem != doc_id:
        notes.append(f"ファイル名（{filename}）と文書ID（{doc_id}）が違います")

    doc = {"doc_id": doc_id, "doc_type": doc_type, "doc_category": kind["doc_category"],
           "title": titles[0] if titles else None, "source_file": filename}
    if not titles:
        problems.append("表題（スタイル「表題」の段落）がありません")

    labels = INFO_LABELS[doc_type]
    for label, column in labels.items():
        doc[column] = info.get(label) or None
    unused = [k for k in info if k not in labels and k not in ("文書ID", "文書の種類")]
    if unused:
        notes.append(f"DBに入れない項目：{'、'.join(unused)}")

    for column in REQUIRED[doc_type]:
        if not doc.get(column):
            problems.append(f"必須の項目が空です：{column}")
    for column in DATE_COLUMNS & doc.keys():
        if doc[column] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc[column]):
            problems.append(f"{column} の日付の形が違います：{doc[column]}（例：2026-09-10）")
    for column, allowed in ALLOWED.items():
        if doc.get(column) and doc[column] not in allowed:
            problems.append(f"{column} の値「{doc[column]}」は使えません（使える値：{'、'.join(allowed)}）")
    if doc_type == "project" and doc.get("owner_dept_id"):
        name = doc["owner_dept_id"]
        doc["owner_dept_id"] = dept_ids.get(name)
        if doc["owner_dept_id"] is None:
            problems.append(f"主管部門「{name}」が部署マスタにありません")

    # ---------- 提案者・体制 → document_authors ----------
    authors = []
    header, *rows = tables[1]
    if header[:4] != ["役割", "社員番号", "氏名", "部署"]:
        problems.append(f"提案者（体制）の表の見出しが違います：{'｜'.join(header)}")
        rows = []
    for role, emp_no, name, dept_name, *_ in rows:
        if role not in AUTHOR_ROLES[doc_type]:
            problems.append(f"役割「{role}」は使えません（使える値：{'、'.join(AUTHOR_ROLES[doc_type])}）")
        emp = employees.get(emp_no)
        if emp is None:
            problems.append(f"社員番号「{emp_no}」が社員マスタにありません")
        elif emp["name"] != name:
            notes.append(f"社員番号 {emp_no} の氏名が社員マスタ（{emp['name']}）と違います：{name}")
        dept_id = dept_ids.get(dept_name)
        if dept_id is None:
            problems.append(f"部署「{dept_name}」が部署マスタにありません")
        authors.append({"doc_id": doc_id, "emp_no": emp_no, "role": role, "dept_id_at_time": dept_id})
    if not authors:
        problems.append("提案者（体制）が1人もいません")
    dup = {a["emp_no"] for a in authors if sum(b["emp_no"] == a["emp_no"] for b in authors) > 1}
    if dup:
        problems.append(f"同じ社員番号が2回出てきます：{'、'.join(sorted(dup))}")

    # ---------- 見出し1で区切った章 → document_sections ----------
    sections, current = [], None
    for kind_, value in blocks:
        if kind_ == "heading":
            current = {"name": value, "paras": []}
            sections.append(current)
        elif kind_ == "text" and current is not None:
            current["paras"].append(value)
    roles = SECTION_ROLES[doc_type]
    section_rows = []
    for no, s in enumerate(sections, start=1):
        role = roles.get(s["name"])
        if role is None:
            problems.append(f"章「{s['name']}」の役割が決まっていません（使える章：{'、'.join(roles)}）")
        if not s["paras"]:
            problems.append(f"章「{s['name']}」の本文が空です")
        section_rows.append({"doc_id": doc_id, "section_no": no, "section_name": s["name"],
                             "section_role": role, "body": "\n".join(s["paras"])})
    if not section_rows:
        problems.append("章（スタイル「見出し1」の段落）が1つもありません")

    return ({"documents": [doc], "document_sections": section_rows, "document_authors": authors},
            problems, notes)


def check_duplicates(results, existing):
    """同時に登録する文書どうしの文書IDの重複と、DBにすでにある文書を調べて、
    ファイル名 → (問題のリスト, 注意のリスト) を返す"""
    found = {}
    for filename, (rows, _, _) in results.items():
        for doc in rows["documents"]:
            found.setdefault(doc["doc_id"], []).append(filename)
    checks = {filename: ([], []) for filename in results}
    for doc_id, filenames in found.items():
        if len(filenames) > 1:
            for f in filenames:
                checks[f][0].append(f"同じ文書ID {doc_id} のファイルが複数あります：{'、'.join(filenames)}")
        if doc_id in existing:
            old = existing[doc_id]
            for f in filenames:
                checks[f][1].append(f"文書ID {doc_id} はDBに登録済みです（{old['title']}／{old['source_file']}）。"
                                    "登録すると、文書・章・著者を上書きします")
    return checks


def main():
    args = sys.argv[1:]
    save = "--save" in args
    overwrite = "--overwrite" in args
    paths = [Path(p) for p in args if not p.startswith("--")]
    if not paths:
        sys.exit("使い方：python register/word_to_rows.py [--save [--overwrite]] ファイル.docx ...\n"
                 "  （何も付けない）：読んでチェックするだけ。DBには書き込まない\n"
                 "  --save     ：問題がなければDBに書き込む（登録済みの文書があるときは止まる）\n"
                 "  --overwrite：--save と一緒に使うと、登録済みの文書を上書きする")

    from db import connect, existing_docs, load_masters
    sb = connect()
    masters = load_masters(sb)
    results = {path.name: read_word(path.read_bytes(), path.name, masters) for path in paths}
    doc_ids = [d["doc_id"] for rows, _, _ in results.values() for d in rows["documents"]]
    existing = existing_docs(sb, doc_ids)
    for filename, (more_problems, more_notes) in check_duplicates(results, existing).items():
        results[filename][1].extend(more_problems)
        results[filename][2].extend(more_notes)

    has_problem = False
    for filename, (rows, problems, notes) in results.items():
        print(f"\n==================== {filename}")
        for table, items in rows.items():
            print(f"【{table}】{len(items)}件")
            for item in items:
                shown = {k: (v[:40] + "…" if isinstance(v, str) and len(v) > 40 else v)
                         for k, v in item.items()}
                print("   " + json.dumps(shown, ensure_ascii=False))
        for n in notes:
            print(f"   ! {n}")
        for p in problems:
            print(f"   × {p}")
        has_problem |= bool(problems)

    if has_problem:
        sys.exit("\n問題のある文書があります（× の行）。DBには書き込んでいません。")
    if not save:
        print("\nお試し実行のため、DBには書き込んでいません。")
        return
    if existing and not overwrite:
        sys.exit(f"\n登録済みの文書があります（{'、'.join(sorted(existing))}）。"
                 "上書きするときは --overwrite を付けてください。DBには書き込んでいません。")

    # ---------- 書き込む（1文書ずつ） ----------
    from db import save_document
    print()
    for filename, (rows, _, _) in results.items():
        doc_id = rows["documents"][0]["doc_id"]
        r = save_document(sb, rows)
        action = "上書き" if doc_id in existing else "新規登録"
        extra = ""
        if r["removed_sections"] or r["removed_authors"]:
            extra = f"（減った章 {r['removed_sections']}件・著者 {r['removed_authors']}件を削除）"
        print(f"【{action}】{doc_id}：章 {r['sections']}件、著者 {r['authors']}件、キーワード {r['keywords']}件{extra}")
    print("章の埋め込みは空のままです。埋め込みの処理で書き込んでください。")


if __name__ == "__main__":
    main()
