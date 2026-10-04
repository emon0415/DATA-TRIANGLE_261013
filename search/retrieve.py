# =============================================================
# retrieve.py — 候補を集める（いまはベクトル検索だけ）
#
# いまのやり方：DB から章のベクトルをまとめて読み、手元で質問文との類似度を計算する
#   章は千数百本なので、全件と比べても一瞬で終わる
#   DB側にベクトル検索の関数（RPC）ができたら、vector_search の中身だけ差し替える
#
# 全文検索（PGroonga）と、2つの順位の統合（RRF）は、あとでここに足す
# =============================================================
import json

import numpy as np
import pandas as pd

from search.embed import normalize

SECTION_COLS = ("section_id, doc_id, section_no, section_name, section_role, body, embedding_model, embedding, "
                "documents(title, doc_type)")


def load_section_vectors(sb, page=500):
    """DB から章とベクトルを読む。戻り値：(章の表, ベクトルの行列)。行の順番は同じ"""
    rows, start = [], 0
    while True:
        data = (sb.table("document_sections").select(SECTION_COLS)
                .not_.is_("embedding", "null").order("section_id")
                .range(start, start + page - 1).execute().data)
        rows += data
        if len(data) < page:
            break
        start += page
    vecs = []
    for r in rows:
        e = r.pop("embedding")
        vecs.append(json.loads(e) if isinstance(e, str) else e)   # DB からは文字列で返ってくる
        d = r.pop("documents") or {}
        r["title"], r["doc_type"] = d.get("title"), d.get("doc_type")
    return pd.DataFrame(rows), normalize(np.array(vecs, dtype=np.float32))


def vector_search(qvec, sections, matrix, k=200):
    """質問文のベクトルと近い章を、類似度の高い順に k 本返す（列：rank, score と章の情報）"""
    scores = matrix @ qvec
    top = np.argsort(-scores)[:k]
    out = sections.iloc[top].copy()
    out.insert(0, "score", scores[top].round(4))
    out.insert(0, "rank", range(1, len(top) + 1))
    return out.reset_index(drop=True)


def to_documents(hits):
    """章の結果を文書の単位にまとめる（文書の点数＝その文書で最も近い章の点数）"""
    best = hits.sort_values("score", ascending=False).drop_duplicates("doc_id")
    out = best[["doc_id", "title", "doc_type", "score", "section_name"]].rename(
        columns={"section_name": "最も近い章"}).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out