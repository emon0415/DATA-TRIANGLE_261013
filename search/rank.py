# =============================================================
# rank.py — 章の順位から、人の順位を作る（v5。設計は引き継ぎ資料の5-1）
#
#   章の順位（ベクトル検索・全文検索） → RRFで統合 → 章 → 文書 → 人 に集約 → 2つの枠に分ける
#
# この部品はDBにも埋め込みにも触らない。渡されたデータだけで計算するので、
# 検索側（retrieve.py）から呼んでも、手元のJSONで試しても同じ結果になる。
# IDは何でもよい（DBのbigintでも、JSONの文書番号でもよい）。
# =============================================================
from collections import defaultdict
from dataclasses import dataclass

# 役割の重み（5-1の決定。投稿者は未決なので、提案者と同じ1.0を仮置き）
ROLE_WEIGHT = {"提案者": 1.0, "主担当": 1.0, "副担当": 0.6, "責任者": 0.3, "投稿者": 1.0}


# ---------- 1. 統合：RRF ----------
def rrf(rankings, k=60):
    """順位のリスト（それぞれ、よい順のIDの並び）を1つにまとめる。戻り値：{ID: スコア}"""
    score = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            score[item] += 1.0 / (k + rank)
    return dict(score)


# ---------- 2. 検索の対象データ ----------
@dataclass
class Corpus:
    """順位づけに必要なデータ。DBから読んだ行を、IDで引ける形にしたもの"""
    section_doc: dict                 # 章ID → 文書ID
    docs: dict                        # 文書ID → {"doc_type", "result", "pj_status", "date"}（dateは "YYYY-MM-DD"）
    authors: dict                     # 文書ID → [(社員ID, 役割)]
    employees: dict                   # 社員ID → {"dept_id", "is_active"}

    @classmethod
    def from_rows(cls, sections, documents, authors, employees):
        """行（辞書のリスト）から作る。列名は v5 のテーブルに合わせる（doc_id・emp_id は読み替えてよい）"""
        docs = {}
        for d in documents:
            date = d.get("submitted_at") if d["doc_type"] != "project" else d.get("started_at")
            docs[d["doc_id"]] = {"doc_type": d["doc_type"], "result": d.get("result"),
                                 "pj_status": d.get("pj_status"), "date": date}
        auth = defaultdict(list)
        for a in authors:
            auth[a["doc_id"]].append((a["emp_id"], a["role"]))
        return cls(section_doc={s["section_id"]: s["doc_id"] for s in sections}, docs=docs,
                   authors=dict(auth),
                   employees={e["emp_id"]: {"dept_id": e["dept_id"], "is_active": e.get("is_active", True)}
                              for e in employees})


# ---------- 3. 章 → 文書 → 人 ----------
def doc_scores(section_scores, corpus):
    """章のスコアから、文書のスコア（章の最大値）と、その根拠の章を作る"""
    best = {}
    for sid, s in section_scores.items():
        doc = corpus.section_doc.get(sid)
        if doc is None:
            continue
        if doc not in best or s > best[doc][0]:
            best[doc] = (s, sid)
    return best        # {文書ID: (スコア, 根拠の章ID)}


def _year(date):
    return int(str(date)[:4]) if date else None


def _is_proven(doc):
    """実績のある文書：完了したPJ、採択された提案書"""
    return (doc["doc_type"] == "project" and doc["pj_status"] == "完了") or \
           (doc["doc_type"] == "proposal" and doc["result"] == "採択")


def rank_people(section_scores, corpus, searcher=None, depts=None, years=None,
                role_weight=None, beta=0.3, recency=0.0, diversity=0.0, top_n=5, today_year=2026):
    """
    section_scores: {章ID: スコア}（RRFのあと。大きいほどよい）
    searcher: 検索した本人の社員ID（結果から除く）
    depts: 絞り込みの部署ID（複数）。人の今の所属で絞る
    years: 絞り込みの年（複数）。文書の日付の年で絞る
    beta: 2番目に強い文書の加点の割合（経験の厚み）
    recency: 新しさの加点（0で無効。1年古いごとに recency だけ割り引く。最大5年）
    diversity: 同じ部署の人が続いたときの割り引き（0で無効）
    戻り値: {"proven": [...], "hidden": [...]}　各要素は人の情報と根拠の文書
    """
    w = role_weight or ROLE_WEIGHT
    contrib = defaultdict(list)     # 社員ID → [(貢献, 文書ID, 章ID, 役割)]
    for doc_id, (s, sid) in doc_scores(section_scores, corpus).items():
        doc = corpus.docs[doc_id]
        year = _year(doc["date"])
        if years and year not in years:
            continue
        factor = 1.0 - recency * min(max(today_year - year, 0), 5) if (recency and year) else 1.0
        for emp, role in corpus.authors.get(doc_id, []):
            contrib[emp].append((s * w.get(role, 0.0) * factor, doc_id, sid, role))

    people = []
    for emp, items in contrib.items():
        info = corpus.employees.get(emp)
        if info is None or not info["is_active"] or emp == searcher:
            continue
        if depts and info["dept_id"] not in depts:
            continue
        items.sort(key=lambda x: -x[0])
        score = items[0][0] + (beta * items[1][0] if len(items) > 1 else 0.0)
        top_doc = corpus.docs[items[0][1]]
        people.append({"emp": emp, "dept_id": info["dept_id"], "score": score, "frame":
                       "proven" if _is_proven(top_doc) else "hidden",
                       "evidence": [{"doc": d, "section": sid, "role": r, "score": round(c, 6)}
                                    for c, d, sid, r in items[:3]]})

    out = {"proven": [], "hidden": []}
    for frame in out:
        pool = sorted((p for p in people if p["frame"] == frame), key=lambda p: -p["score"])
        out[frame] = _diversify(pool, diversity)[:top_n]
    return out


def _diversify(pool, diversity):
    """同じ部署が続くたびに、スコアを割り引いて選び直す（0なら並べ替えない）"""
    if not diversity:
        return pool
    pool, picked, seen = list(pool), [], defaultdict(int)
    while pool:
        best = max(pool, key=lambda p: p["score"] * (1 - diversity) ** seen[p["dept_id"]])
        pool.remove(best); picked.append(best); seen[best["dept_id"]] += 1
    return picked
