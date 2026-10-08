# =============================================================
# retrieve.py — 候補を集める（ベクトル検索＋全文検索、RRFで統合）
#
# いまのやり方：DB から章のベクトルをまとめて読み、手元で質問文との類似度を計算する
#   章は千数百本なので、全件と比べても一瞬で終わる
#   DB側にベクトル検索の関数（RPC）ができたら、vector_search の中身だけ差し替える
#
# 全文検索：DBの関数 search_sections_fts（SQL/v5_search_fts.sql）を rpc で呼ぶ
# 統合：hybrid_scores() が、2つの章の順位を RRF でまとめ、{章ID: スコア} を返す
#       → search/rank.py の rank_people() にそのまま渡せる
# =============================================================
import json

import numpy as np
import pandas as pd

from search import rank, tokenizer
from search.embed import normalize

# v5：doc_id は bigint。人が読む文書番号（KZ-2024-0147 など）は documents.doc_code にある
SECTION_COLS = ("section_id, doc_id, section_no, section_name, section_role, body, embedding_model, embedding, "
                "documents(doc_code, title, doc_type)")


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
        r["doc_code"], r["title"], r["doc_type"] = d.get("doc_code"), d.get("title"), d.get("doc_type")
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
    out = best[["doc_id", "doc_code", "title", "doc_type", "score", "section_name"]].rename(
        columns={"section_name": "最も近い章"}).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out

# ---------- 全文検索と統合 ----------
FTS_N = 100   # 各順位から取る章の数（仮置き）
RRF_K = 60    # RRFの定数（仮置き）


def fulltext_search(sb, query, n=FTS_N):
    """質問文から語を取り出し、PGroongaで章を検索する。戻り値：[章ID]（スコアの高い順）"""
    q = tokenizer.query_text(query)
    if not q:                      # 名詞が取れない質問文は、全文側は空にする
        return []
    rows = sb.rpc("search_sections_fts", {"q": q, "n": n}).execute().data
    return [r["section_id"] for r in rows]


def hybrid_scores(vector_ids, fts_ids, k=RRF_K, w_vector=1.0, w_fts=1.0):
    """ベクトルと全文の章の順位をRRFで統合する。w_* は比重（0なら、その順位は使わない）。
    片方が空なら、もう片方だけの順位になる。比重は、2つの比率だけが効く（0.5と0.5は1と1と同じ）"""
    pairs = [(r, w) for r, w in ((vector_ids, w_vector), (fts_ids, w_fts)) if r]
    return rank.rrf([r for r, _ in pairs], k=k, weights=[w for _, w in pairs])


# ---------- 看板 ----------
def load_profiles(sb, page=500):
    """看板（profiles）をDBから読む。戻り値：(行のリスト, 社員IDの並び, ベクトルの行列)。
    行は {"emp_id", "profile_text", "generated_at", "embedded_at"}。ベクトルがある人だけが、並びと行列に入る"""
    rows, start = [], 0
    while True:
        data = (sb.table("profiles").select("emp_id,profile_text,generated_at,embedded_at,embedding")
                .order("emp_id").range(start, start + page - 1).execute().data)
        rows += data
        if len(data) < page:
            break
        start += page
    ids, vecs = [], []
    for r in rows:
        e = r.pop("embedding")
        if e is not None:
            ids.append(r["emp_id"])
            vecs.append(json.loads(e) if isinstance(e, str) else e)
    matrix = normalize(np.array(vecs, dtype=np.float32)) if vecs else np.zeros((0, 1), dtype=np.float32)
    return rows, ids, matrix


def profile_similarity(qvec, ids, matrix):
    """質問文のベクトルと、看板のベクトルの類似度（コサイン）。戻り値：{社員ID: 類似度}"""
    if not ids:
        return {}
    return dict(zip(ids, (matrix @ qvec).tolist()))
