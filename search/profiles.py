# =============================================================
# profiles.py — 看板（profiles）の文章を作る。DBもOpenAIも使わない（入れた値だけで決まる）
#
# 看板 ＝ その人のノートを、日付の順に並べた1つの文章。
#   ・文書由来のノート     … 文書1件につき1枚。意欲（提案・アイデア）か、実行力（PJ）かは、文書の種類で決まる
#   ・キャリアシートのノート … 本人の入力（文書を持たない）。「現在の職務」「将来やりたいこと」
#                              「そのために取り組んでいること」の3項目、各1000字まで
# ノートの本文は、文書の章の抜粋（短く切ったもの）。GPTの要約には差し替えられる（body_source）。
# 看板の文章は、順位づけと、推薦文の補足にだけ使う。画面にも推薦理由にも出さない。
#
# DBとのやりとり、埋め込みは profiles_store.py。
# =============================================================
from collections import defaultdict

NOTE_CHARS = 300        # ノート1枚の本文の長さの上限（文字）
TEXT_MAX = 6000         # 看板の文章の長さの上限。DBの check（6000文字）に合わせる。キャリアシート3項目で最大3000字
TAG_N = 5               # ノートに添えるタグの数
TAG_MAX_DF = 0.05       # この割合より多くの文書に出る語は、タグにしない（「部門」「記録」など）

# どの章を、ノートの本文にするか（章の役割 section_role で選ぶ。章名はWordの見出しで、文書の種類で違う）
NOTE_ROLES = {
    "proposal": ["課題意識", "行動案"],         # 問題点、提案内容
    "idea":     ["課題意識", "行動案"],         # 困りごと、アイデア
    "project":  ["目的", "行動", "成果"],       # 目的、実施内容、成果
}
KIND = {"proposal": "意欲", "idea": "意欲", "project": "実行力"}

# ノートに入れない文書の状態（レビューで決める項目。空なら、すべて入れる）
EXCLUDE_RESULTS = ()     # 例：("審査中",)
EXCLUDE_PJ_STATUS = ()   # 例：("計画中",)


def clip(text, limit):
    """limit 字までに切る。できるだけ文の区切り（。）で切り、文の途中で終わらせない。
    区切りが前のほうにしかないとき（limit の半分より手前）は、文の途中で切って「…」を付ける"""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind("。")
    if cut + 1 >= limit // 2:
        return head[:cut + 1]
    return head[:limit - 1] + "…"


def note_body(sections, doc_type, limit=NOTE_CHARS):
    """文書の章から、ノートの本文を作る。章ごとに、同じ長さの枠を割り当てて切る。
    sections: その文書の章（section_role、body、section_no を持つ辞書）のリスト"""
    by_role = {}
    for s in sorted(sections, key=lambda s: s["section_no"]):
        by_role.setdefault(s["section_role"], s["body"])
    picked = [by_role[r] for r in NOTE_ROLES[doc_type] if r in by_role]
    if not picked:
        return ""
    share = limit // len(picked)
    return " ".join(clip(b, share) for b in picked)


def doc_label(doc):
    t = doc["doc_type"]
    if t == "proposal":
        return f"提案（{doc['result']}）"
    if t == "project":
        return f"PJ（{doc['pj_status']}）"
    return "アイデア投稿"


# 文書の種類ごとの名札（画面の表示用）：(絵文字, 種類の名前, 一言の説明)
DOC_KINDS = {
    "proposal": ("📝", "改善提案", "現場から出された改善の提案です。結果の状態は、名札のかっこに出ます。"),
    "project": ("🛠", "プロジェクト", "実際に動いたプロジェクトの記録です。"),
    "idea": ("💡", "アイデア投稿", "現場の困りごとと、そのアイデアです。審査はされていません。"),
}


def doc_icon(doc):
    return DOC_KINDS.get(doc["doc_type"], DOC_KINDS["idea"])[0]


def doc_badge(doc):
    """名札。例：📝 改善提案（採択）／🛠 プロジェクト（完了）／💡 アイデア投稿"""
    icon, name, _ = DOC_KINDS.get(doc["doc_type"], DOC_KINDS["idea"])
    state = {"proposal": doc.get("result"), "project": doc.get("pj_status")}.get(doc["doc_type"])
    return f"{icon} {name}（{state}）" if state else f"{icon} {name}"


def doc_kind_note(doc):
    return DOC_KINDS.get(doc["doc_type"], DOC_KINDS["idea"])[2]


def doc_date(doc):
    """並べ替えと表示に使う日付（YYYY-MM-DD）。提案・アイデアは提出日、PJは開始日"""
    return str(doc.get("submitted_at") or doc.get("started_at") or "")[:10]


def skip_doc(doc):
    return (doc["doc_type"] == "proposal" and doc.get("result") in EXCLUDE_RESULTS) or \
           (doc["doc_type"] == "project" and doc.get("pj_status") in EXCLUDE_PJ_STATUS)


def common_keywords(keywords, n_docs):
    """多くの文書に出る語（タグにしない語）の集合。keywords: {doc_id: [(語, 回数), ...]}"""
    df = defaultdict(int)
    for kws in keywords.values():
        for kw in {k for k, _ in kws}:
            df[kw] += 1
    return {kw for kw, c in df.items() if n_docs and c / n_docs > TAG_MAX_DF}


def doc_tags(kws, common, n=TAG_N):
    ranked = sorted((kw for kw in kws if kw[0] not in common), key=lambda kw: (-kw[1], kw[0]))
    return [k for k, _ in ranked[:n]]


def format_note(n):
    """看板の文章に並べる、ノート1枚分"""
    head = (f"【{n['kind']}｜{n['label']}｜{n['role']}｜{n['date']}】" if n["kind"] != "キャリアシート"
            else f"【キャリアシート｜{n['label']}｜{n['date']}】")
    if n.get("title"):
        head += n["title"]
    lines = [head, n["body"]]
    if n.get("tags"):
        lines.append("タグ：" + " ".join(n["tags"]))
    return "\n".join(lines)


def assemble(notes, limit=TEXT_MAX):
    """ノートを日付の順（古い→新しい）に並べて、看板の文章にする。
    長すぎるときは、古いノートから外す（ノート自体は残る）。戻り値：(文章, 使ったノートの数)"""
    ordered = sorted(notes, key=lambda n: (n["date"], n.get("doc_id") or 0))
    blocks = [format_note(n) for n in ordered]
    while blocks and len("\n\n".join(blocks)) > limit:
        blocks.pop(0)
    return "\n\n".join(blocks), len(blocks)


def build(docs, sections, authors, keywords, memos=None, emp_ids=None):
    """人ごとのノートと看板の文章を作る。

    docs      {doc_id: {doc_type, title, result, pj_status, submitted_at, started_at}}
    sections  [{doc_id, section_no, section_role, body}]
    authors   [{doc_id, emp_id, role}]
    keywords  {doc_id: [(語, 回数), ...]}
    memos     {emp_id: [{"body", "memo_kind", "updated_at"}, ...]}  キャリアシート（本人入力）
    emp_ids   作る人を絞るときだけ（登録のたびの作り直し）
    戻り値    {emp_id: {"notes": [ノート], "text": 看板の文章, "used": 文章に使ったノートの数}}
    ノート：{kind, label, date, role, title, body, tags, doc_id}（キャリアシートは doc_id が None、label は項目名）"""
    memos = memos or {}
    by_doc = defaultdict(list)
    for s in sections:
        by_doc[s["doc_id"]].append(s)
    common = common_keywords(keywords, len(docs))
    bodies = {d: note_body(by_doc.get(d, []), doc["doc_type"]) for d, doc in docs.items()}

    per_emp = defaultdict(list)
    for a in authors:
        doc = docs.get(a["doc_id"])
        if not doc or skip_doc(doc) or not bodies[a["doc_id"]]:
            continue
        if emp_ids is not None and a["emp_id"] not in emp_ids:
            continue
        per_emp[a["emp_id"]].append({
            "kind": KIND[doc["doc_type"]], "label": doc_label(doc), "date": doc_date(doc),
            "role": a["role"], "title": doc["title"], "body": bodies[a["doc_id"]],
            "tags": doc_tags(keywords.get(a["doc_id"], []), common), "doc_id": a["doc_id"],
        })
    for emp, items in memos.items():
        if emp_ids is not None and emp not in emp_ids:
            continue
        for m in items:
            per_emp[emp].append({"kind": "キャリアシート", "label": m.get("memo_kind") or "本人入力",
                                 "date": str(m.get("updated_at") or m.get("created_at"))[:10], "role": None,
                                 "title": None, "body": clip(m["body"], 1000), "tags": [], "doc_id": None})
    out = {}
    for emp, notes in per_emp.items():
        text, used = assemble(notes)
        out[emp] = {"notes": notes, "text": text, "used": used}
    return out
