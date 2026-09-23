# =============================================================
# verify_ingest.py — Word取り込みの検証（試作）
#
# 指定フォルダ内のWord文書を自動で読み、DBに入れられる形に変換できるかを
# 検証して、結果を画面に表示する。DBへの書き込みと埋め込みは行わない。
#
# 使い方：
#   python verify_ingest.py <フォルダ>            結果の一覧だけ表示
#   python verify_ingest.py <フォルダ> --detail   取り出した中身も表示
# =============================================================
import re
import sys
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

# ---------- テンプレートの定義 ----------
PROPOSAL = {
    "id_key": "提案番号",
    "required_meta": ["提案番号", "業務分類", "表題", "提出日", "提案区分", "判定"],
    "categories": {"1-2", "1-4"},
    "sections": {  # 章名: (役割, 必須)
        "現状": ("背景", True),
        "問題点": ("課題意識", True),
        "提案内容": ("行動案", True),
        "期待効果": ("目標", True),
        "実施上の課題": ("実施上の課題", False),
    },
}
PROJECT = {
    "id_key": "PJ番号",
    "required_meta": ["PJ番号", "業務分類", "プロジェクト名", "状態", "開始日", "終了予定日", "主管部門", "最終更新日"],
    "categories": {"2-1"},
    "sections": {
        "背景": ("背景", True),
        "目的": ("目的", True),
        "実施内容": ("行動", True),
        "成果": ("成果", False),  # 完了・中止のときだけ必須（後で判定）
    },
}
PROPOSAL_AREAS = {"営業推進", "業務効率", "品質・設備保全", "その他"}
RESULTS = {"審査中", "採択", "不採択", "保留"}
PJ_STATUSES = {"計画中", "進行中", "完了", "中止"}
PJ_ROLES = {"責任者", "主担当", "副担当"}

HEADING = re.compile(r"^(\d+)\.\s*(.+)$")
DATE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")
NUMERIC_CELL = re.compile(r"^[0-9,.%約件時間分回社—QY\s〜年月平均]+$")


# ---------- 読み取り ----------
def iter_blocks(doc):
    """段落と表を、文書に出てくる順番どおりに返す"""
    for el in doc.element.body.iterchildren():
        tag = el.tag.split("}")[1]
        if tag == "p":
            yield Paragraph(el, doc)
        elif tag == "tbl":
            yield Table(el, doc)


def table_rows(tbl):
    return [[c.text.strip() for c in r.cells] for r in tbl.rows]


def to_date(text):
    m = DATE.match(text or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def parse(path):
    """1ファイルを読み、(結果, エラー一覧, 警告一覧) を返す"""
    errors, warnings = [], []
    doc = Document(str(path))
    blocks = list(iter_blocks(doc))
    tables = [b for b in blocks if isinstance(b, Table)]
    if len(tables) < 2:
        return None, ["メタ情報の表と、提案者または体制の表が見つからない"], []

    # メタ情報（1つ目の表）
    meta = {}
    for row in table_rows(tables[0]):
        if len(row) >= 2:
            meta[row[0]] = row[1]

    if "提案番号" in meta:
        doc_type, spec = "proposal", PROPOSAL
    elif "PJ番号" in meta:
        doc_type, spec = "project", PROJECT
    else:
        return None, ["文書種別を判定できない（提案番号・PJ番号のどちらもない）"], []

    for k in spec["required_meta"]:
        if not meta.get(k):
            errors.append(f"必須項目「{k}」が空")

    category = (meta.get("業務分類") or "").split(" ")[0]
    if category and category not in spec["categories"]:
        errors.append(f"業務分類「{category}」はこの文書種別で使えない")

    # 日付の変換
    date_keys = ["提出日", "判定日"] if doc_type == "proposal" else ["開始日", "終了予定日", "終了日", "最終更新日"]
    dates = {}
    for k in date_keys:
        if meta.get(k):
            d = to_date(meta[k])
            if d is None:
                errors.append(f"「{k}」の日付形式が読めない：{meta[k]}")
            dates[k] = d

    # 種別ごとの値の検証
    if doc_type == "proposal":
        if meta.get("提案区分") and meta["提案区分"] not in PROPOSAL_AREAS:
            errors.append(f"提案区分「{meta['提案区分']}」は選択肢にない")
        result = meta.get("判定")
        if result and result not in RESULTS:
            errors.append(f"判定「{result}」は選択肢にない")
        if result == "審査中" and meta.get("判定日"):
            errors.append("審査中なのに判定日が入っている")
    else:
        status = meta.get("状態")
        if status and status not in PJ_STATUSES:
            errors.append(f"状態「{status}」は選択肢にない")
        if status in ("完了", "中止") and not meta.get("終了日"):
            errors.append(f"状態が{status}なのに終了日がない")
        if status in ("計画中", "進行中") and meta.get("終了日"):
            errors.append(f"状態が{status}なのに終了日が入っている")

    # 提案者・体制（2つ目の表）
    rows = table_rows(tables[1])
    header, body = rows[0], rows[1:]
    people = []
    if doc_type == "proposal":
        for r in body:
            people.append({"role": "提案者", "name": r[0], "emp_no": r[1], "dept_text": r[2]})
        if len(people) != 1:
            errors.append(f"提案者は1名のはずが{len(people)}名")
    else:
        for r in body:
            if r[0] not in PJ_ROLES:
                errors.append(f"体制の枠「{r[0]}」はテンプレートにない")
            people.append({"role": r[0], "name": r[1], "emp_no": r[2], "dept_text": r[3]})
        for role in ("責任者", "主担当"):
            n = sum(p["role"] == role for p in people)
            if n != 1:
                errors.append(f"{role}は1名のはずが{n}名")
        emp_nos = [p["emp_no"] for p in people]
        if len(emp_nos) != len(set(emp_nos)):
            errors.append("同じ社員番号が体制に2回出てくる（兼任は主担当として1行で記録）")
    for p in people:
        if not re.fullmatch(r"\d+", p["emp_no"] or ""):
            errors.append(f"{p['name'] or '（氏名なし）'}の社員番号が不正：「{p['emp_no']}」")
        if not p["dept_text"]:
            errors.append(f"{p['name']}の所属が空")

    # 本文の章
    sections, current = [], None
    start = blocks.index(tables[1]) + 1
    for b in blocks[start:]:
        if isinstance(b, Paragraph):
            text = b.text.strip()
            m = HEADING.match(text)
            if m:
                name = m.group(2)
                if name not in spec["sections"]:
                    errors.append(f"テンプレートにない章「{text}」")
                    current = None
                    continue
                role, _ = spec["sections"][name]
                current = {"no": int(m.group(1)), "name": name, "role": role, "body": []}
                sections.append(current)
                continue
            if text and text != "以上" and current is not None:
                current["body"].append(text)  # キャプションもここで本文に入る
        elif isinstance(b, Table) and current is not None:
            # 文章の表は行を文にして本文に含める。数値の表は除外する
            rows = table_rows(b)
            cells = [c for r in rows[1:] for c in r]
            numeric = sum(bool(NUMERIC_CELL.match(c)) for c in cells)
            if cells and numeric / len(cells) < 0.5:
                current["body"] += ["：".join(r) for r in rows[1:]]

    found = {s["name"] for s in sections}
    for name, (_, required) in spec["sections"].items():
        if required and name not in found:
            errors.append(f"必須の章「{name}」がない")
    if doc_type == "project":
        status = meta.get("状態")
        if status in ("完了", "中止") and "成果" not in found:
            errors.append(f"状態が{status}なのに成果の章がない")
        if status in ("計画中", "進行中") and "成果" in found:
            warnings.append(f"状態が{status}なのに成果の章がある")
    names = [s["name"] for s in sections]
    if len(names) != len(set(names)):
        errors.append("同じ章が2回出てくる")
    for s in sections:
        s["body"] = "\n".join(s["body"])
        if not s["body"]:
            errors.append(f"章「{s['name']}」の本文が空")
        elif len(s["body"]) < 30:
            warnings.append(f"章「{s['name']}」が{len(s['body'])}字と短い")

    result = {
        "doc_id": meta.get(spec["id_key"]),
        "doc_type": doc_type,
        "doc_category": category,
        "title": meta.get("表題") or meta.get("プロジェクト名"),
        "meta": meta,
        "dates": dates,
        "people": people,
        "sections": sections,
        "source_file": path.name,
    }
    return result, errors, warnings


# ---------- フォルダの処理 ----------
def scan(folder, detail=False):
    folder = Path(folder)
    files = sorted(p for p in folder.iterdir() if p.is_file())
    ok, ng, skipped = [], [], []
    parsed = []  # (ファイル名, 結果, エラー, 警告)

    for path in files:
        if path.name.startswith("~$"):
            skipped.append((path.name, "Wordの一時ファイル"))
            continue
        if path.suffix.lower() != ".docx":
            skipped.append((path.name, f"対象外の形式（{path.suffix or '拡張子なし'}）"))
            continue
        try:
            result, errors, warnings = parse(path)
        except Exception as e:  # 壊れたファイルでも全体を止めない
            result, errors, warnings = None, [f"読み込めない：{type(e).__name__}"], []
        parsed.append((path.name, result, errors, warnings))

    # 重複は読む順番に依存させない。同じIDを持つファイルはすべて止める
    by_id = {}
    for name, result, _, _ in parsed:
        if result and result["doc_id"]:
            by_id.setdefault(result["doc_id"], []).append(name)
    for name, result, errors, warnings in parsed:
        if result and result["doc_id"] and len(by_id[result["doc_id"]]) > 1:
            others = "、".join(n for n in by_id[result["doc_id"]] if n != name)
            errors.append(f"文書ID「{result['doc_id']}」が {others} と重複（どれが正しいか人が判断する）")
        if errors:
            ng.append((name, errors, warnings))
        else:
            ok.append((name, result, warnings))

    # ---------- 表示 ----------
    print(f"フォルダ：{folder}")
    print(f"ファイル {len(files)}件 ／ 取り込み可 {len(ok)}件 ／ 取り込み不可 {len(ng)}件 ／ 対象外 {len(skipped)}件\n")

    print("【取り込み可】")
    for name, r, warns in ok:
        kind = "提案" if r["doc_type"] == "proposal" else "PJ"
        print(f"  ○ {name}  [{kind}] {r['doc_id']}  人{len(r['people'])}名  章{len(r['sections'])}")
        for w in warns:
            print(f"      警告：{w}")
        if detail:
            for p in r["people"]:
                print(f"      {p['role']}：{p['name']}（{p['emp_no']}）{p['dept_text']}")
            for s in r["sections"]:
                print(f"      {s['no']}. {s['name']}〔{s['role']}〕{len(s['body'])}字：{s['body'][:30]}…")
    print("\n【取り込み不可】")
    for name, errs, warns in ng:
        print(f"  × {name}")
        for e in errs:
            print(f"      {e}")
    print("\n【対象外】")
    for name, reason in skipped:
        print(f"  - {name}：{reason}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方：python verify_ingest.py <フォルダ> [--detail]")
        sys.exit(1)
    scan(sys.argv[1], detail="--detail" in sys.argv)
