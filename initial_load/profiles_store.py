# =============================================================
# profiles_store.py — 看板をDBから作り、DBに書く（profile_notes と profiles）
#
# 流れ：文書・章・著者・キーワード・カルテをDBから読む → profiles.build でノートと文章を作る
#       → profile_notes に書く → profiles に書く（文章が変わった人だけ、埋め込みも作り直す）
#
#   rebuild(sb)                      全員分を作り直す（初期ロード）
#   rebuild(sb, emp_ids={5, 9})      この人たちだけ（文書の登録のあとに、著者の分を作り直す）
#
# 守ること：
#   ・本人が書いたカルテのノートは、読むだけで書き換えない
#   ・要約（body_source='要約'）にしたノートは、抜粋で上書きしない
#   ・文書が変わって、ノートが作れなくなったとき（章が消えた、など）の古いノートは、消さずに残す
# =============================================================
from datetime import datetime, timezone

from search import profiles as P

CHUNK = 200
PAGE = 1000


def fetch_all(sb, table, cols, order):
    """表を全部読む。ページをまたいでも順番がぶれないように、一意に決まる列で並べる"""
    out, start = [], 0
    while True:
        q = sb.table(table).select(cols)
        for c in order:
            q = q.order(c)
        data = q.range(start, start + PAGE - 1).execute().data
        out += data
        if len(data) < PAGE:
            return out
        start += PAGE


def read_all(sb):
    docs = {d["doc_id"]: d for d in fetch_all(
        sb, "documents", "doc_id,doc_type,title,result,pj_status,submitted_at,started_at", ["doc_id"])}
    sections = fetch_all(sb, "document_sections", "doc_id,section_no,section_role,body", ["doc_id", "section_no"])
    authors = fetch_all(sb, "document_authors", "doc_id,emp_id,role", ["doc_id", "emp_id"])
    keywords = {}
    for k in fetch_all(sb, "document_keywords", "doc_id,keyword,count", ["doc_id", "keyword"]):
        keywords.setdefault(k["doc_id"], []).append((k["keyword"], k["count"]))
    notes = fetch_all(sb, "profile_notes", "note_id,emp_id,doc_id,body,body_source,created_at", ["note_id"])
    memos = {}
    for n in notes:
        if n["body_source"] == "本人入力":
            memos.setdefault(n["emp_id"], []).append(n)
    profiles = {p["emp_id"]: p for p in fetch_all(sb, "profiles", "emp_id,profile_text,embedded_at", ["emp_id"])}
    return docs, sections, authors, keywords, notes, memos, profiles


def merge_summaries(built, notes):
    """要約にしたノートは、本文を要約のまま使う（看板の文章も、それで作り直す）"""
    summary = {(n["emp_id"], n["doc_id"]): n["body"] for n in notes if n["body_source"] == "要約"}
    for emp, item in built.items():
        for n in item["notes"]:
            body = summary.get((emp, n["doc_id"]))
            if body:
                n["body"], n["source"] = body, "要約"
        item["text"], item["used"] = P.assemble(item["notes"])
    return built


def rebuild(sb, emp_ids=None, embed=True, dry_run=False):
    """戻り値：{"people": 人数, "notes": 書いたノート数, "profiles": 書いた看板数, "embedded": 埋め込んだ数}"""
    docs, sections, authors, keywords, notes, memos, profiles = read_all(sb)
    built = P.build(docs, sections, authors, keywords, memos=memos, emp_ids=emp_ids)
    merge_summaries(built, notes)

    summarized = {(n["emp_id"], n["doc_id"]) for n in notes if n["body_source"] == "要約"}
    note_rows = [{"emp_id": emp, "doc_id": n["doc_id"], "body": n["body"], "body_source": "抜粋"}
                 for emp, item in built.items() for n in item["notes"]
                 if n["doc_id"] is not None and (emp, n["doc_id"]) not in summarized]
    now = datetime.now(timezone.utc).isoformat()
    todo = [(emp, item) for emp, item in built.items()
            if emp not in profiles or profiles[emp]["profile_text"] != item["text"] or not profiles[emp]["embedded_at"]]
    result = {"people": len(built), "notes": len(note_rows), "profiles": len(todo), "embedded": 0, "built": built}
    if dry_run:
        return result

    for start in range(0, len(note_rows), CHUNK):
        part = [dict(r, updated_at=now) for r in note_rows[start:start + CHUNK]]
        sb.table("profile_notes").upsert(part, on_conflict="emp_id,doc_id").execute()

    vectors = [None] * len(todo)
    if embed and todo:
        from search import embed as emb
        vectors = emb.embed_texts([item["text"] for _, item in todo])
        result["embedded"] = len(todo)
    for start in range(0, len(todo), CHUNK):
        rows = []
        for (emp, item), vec in zip(todo[start:start + CHUNK], vectors[start:start + CHUNK]):
            row = {"emp_id": emp, "profile_text": item["text"], "source_doc_count": item["used"],
                   "model": None, "generated_at": now}
            if vec is not None:
                from search import embed as emb
                row.update(embedding=vec, embedding_model=emb.MODEL, embedded_at=now)
            else:   # 埋め込みを作らないときは、古いベクトルを残さない（文章と食い違うため）
                row.update(embedding=None, embedding_model=None, embedded_at=None)
            rows.append(row)
        sb.table("profiles").upsert(rows, on_conflict="emp_id").execute()
    return result
