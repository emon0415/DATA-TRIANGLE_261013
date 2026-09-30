# =============================================================
# db.py — Supabase への接続と、登録タブで使う問い合わせ
#
# 接続情報は data-triangle フォルダ直下の .env から読む
#   SUPABASE_URL、SUPABASE_KEY
# =============================================================
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent


def connect():
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(ROOT / ".env")
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError(".env に SUPABASE_URL と SUPABASE_KEY が見つかりません。")
    return create_client(url, key)


def fetch_all(sb, table, cols, page=1000):
    """1回に返ってくる行数に上限があるので、ページに分けてすべて読む"""
    out, start = [], 0
    while True:
        data = sb.table(table).select(cols).range(start, start + page - 1).execute().data
        out += data
        if len(data) < page:
            return out
        start += page


def load_masters(sb):
    """部署名→部署ID と 社員番号→社員 の辞書をDBから作る（読むだけ）"""
    depts = fetch_all(sb, "departments", "dept_id, dept_name")
    emps = fetch_all(sb, "employees", "emp_no, name")
    return ({d["dept_name"]: d["dept_id"] for d in depts},
            {str(e["emp_no"]): e for e in emps})


def existing_docs(sb, doc_ids):
    """DBにすでにある文書を {文書ID: {"title": 表題, "source_file": ファイル名}} で返す（読むだけ）"""
    doc_ids = sorted({d for d in doc_ids if d})
    if not doc_ids:
        return {}
    res = (sb.table("documents").select("doc_id, title, source_file")
           .in_("doc_id", doc_ids).execute())
    return {r["doc_id"]: {"title": r["title"], "source_file": r["source_file"]} for r in res.data}


def save_document(sb, rows):
    """1文書分の行（word_to_rows.read_word の結果）をDBに書き込む。登録済みなら上書きする。

    ・章は (doc_id, section_no) で上書きし、section_id を変えない。
      cluster_members が section_id を参照しているため、章を消して入れ直すことはしない
    ・上書きした章の埋め込みは空に戻す（本文が変わっているかもしれないため。あとで埋め込み直す）
    ・新しい版で減った章と著者だけを消す
    ・途中で失敗しても、同じWordでもう一度実行すれば正しい状態になる"""
    doc = rows["documents"][0]
    doc_id = doc["doc_id"]
    sections = [{**s, "embedding": None, "embedding_model": None, "embedded_at": None}
                for s in rows["document_sections"]]
    authors = rows["document_authors"]

    # 文書 → 章 → 著者の順（章と著者は文書を参照しているため）
    sb.table("documents").upsert(doc, on_conflict="doc_id").execute()
    sb.table("document_sections").upsert(sections, on_conflict="doc_id,section_no").execute()
    removed_sections = (sb.table("document_sections").delete()
                        .eq("doc_id", doc_id).gt("section_no", len(sections)).execute().data)
    sb.table("document_authors").upsert(authors, on_conflict="doc_id,emp_no").execute()
    keep = [a["emp_no"] for a in authors]
    removed_authors = (sb.table("document_authors").delete()
                       .eq("doc_id", doc_id).not_.in_("emp_no", keep).execute().data)
    return {"sections": len(sections), "authors": len(authors),
            "removed_sections": len(removed_sections), "removed_authors": len(removed_authors)}
