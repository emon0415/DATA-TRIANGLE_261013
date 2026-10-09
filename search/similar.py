# =============================================================
# similar.py — 「仲間を探す」：看板の近さで、自分と考えの近い人を見つける
#
#   load_vectors(sb)        … 全員の看板のベクトルをDBから読む（起動時に1回。画面側でキャッシュする）
#   find_similar(...)       … 1人を選んで、近い人を返す
#   shared_terms(a, b)      … 2人の看板に共通する語（「なぜ近いか」の表示用）
#
# 点数の考え方：
#   近さ   ＝ 看板のベクトルのコサイン類似度（0〜1。大きいほど近い）
#   調整後 ＝ 近さ × 同じ部署の係数 × (1 + 加点の強さ × 重なりの少なさ)
#   重なりの少なさ ＝ 1 − 2人が関わった文書の重なり（Jaccard）。同じ文書に関わっていないほど1に近い
#   足切り ＝ 調整後の点が基準未満の人は出さない。誰も残らなければ「おすすめなし」
#   出す人数は、足切りを通った人の上位N人まで
# =============================================================
import json

import numpy as np

DEFAULTS = {"min_sim": 0.5, "top_n": 5, "same_dept": 0.3, "overlap_boost": 0.0}
SAME_DEPT_STEPS = [1.0, 0.6, 0.3]               # サイドバーで選ぶ段階（1.0＝変えない）
# 看板の文章の書式に出てくるだけで、中身を表さない語（「共通する語」に出さない）
FORM_WORDS = {"タグ", "意欲", "実行力", "提案", "提案者", "時間", "抜粋", "要約", "本人入力", "キャリアシート",
              "現在の職務", "将来やりたいこと", "取り組み", "担当", "主担当", "副担当", "責任者", "投稿者", "文書"}


def load_vectors(sb, page=500):
    """看板のベクトルを全員分読む。戻り値：(社員IDのリスト, 正規化したベクトルの行列)"""
    rows, start = [], 0
    while True:
        data = (sb.table("profiles").select("emp_id,embedding").not_.is_("embedding", "null")
                .order("emp_id").range(start, start + page - 1).execute().data)
        rows += data
        if len(data) < page:
            break
        start += page
    ids = [r["emp_id"] for r in rows]
    if not rows:
        return ids, np.zeros((0, 1), dtype=np.float32)
    m = np.array([json.loads(r["embedding"]) if isinstance(r["embedding"], str) else r["embedding"] for r in rows], dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return ids, m / np.where(n == 0, 1, n)


def overlap(docs_a, docs_b):
    """関わった文書の重なり（Jaccard）。どちらも文書がなければ0"""
    a, b = set(docs_a), set(docs_b)
    return len(a & b) / len(a | b) if (a | b) else 0.0


def find_similar(emp, ids, matrix, emps, person_docs, min_sim=DEFAULTS["min_sim"], top_n=DEFAULTS["top_n"],
                 same_dept=DEFAULTS["same_dept"], overlap_boost=DEFAULTS["overlap_boost"]):
    """emp（社員ID）に近い人を返す。
    emps: {社員ID: {"dept_id", "is_active", ...}}／person_docs: {社員ID: [(文書ID, 役割)]}
    足切りは、係数を掛けたあとの点（調整後）に掛ける
    戻り値：{"status": "ok"|"no_profile"|"none", "people": [{"emp", "sim", "score", "same_dept", "overlap"}],
             "candidates": 足切りの前の人数, "passed": 足切りを通った人数}"""
    if emp not in ids:
        return {"status": "no_profile", "people": [], "candidates": 0, "passed": 0}
    sims = matrix @ matrix[ids.index(emp)]
    mine = emps[emp]
    mine_docs = [d for d, _ in person_docs.get(emp, [])]
    rows = []
    for i, other in enumerate(ids):
        if other == emp or not emps.get(other, {}).get("is_active", True):
            continue
        same = emps[other]["dept_id"] == mine["dept_id"]
        ov = overlap(mine_docs, [d for d, _ in person_docs.get(other, [])])
        sim = float(sims[i])
        score = sim * (same_dept if same else 1.0) * (1 + overlap_boost * (1 - ov))
        rows.append({"emp": other, "sim": sim, "score": score, "same_dept": same, "overlap": ov})
    passed = [r for r in rows if r["score"] >= min_sim]
    passed.sort(key=lambda r: -r["score"])
    return {"status": "ok" if passed else "none", "people": passed[:top_n], "candidates": len(rows), "passed": len(passed)}


def shared_terms(text_a, text_b, top_n=5):
    """2人の看板の文章に共通する語（形態素解析で取り出した名詞）。2人の出現回数の少ないほうが多い順"""
    from search import tokenizer
    a, b = dict(tokenizer.keyword_counts(text_a, 200)), dict(tokenizer.keyword_counts(text_b, 200))
    common = sorted((set(a) & set(b)) - FORM_WORDS, key=lambda w: (-min(a[w], b[w]), w))
    return common[:top_n]


def distribution(ids, matrix, sample=None):
    """全員の組み合わせの類似度の分布（足切りの基準を決める目安）。戻り値：{パーセンタイル: 類似度}"""
    n = len(ids)
    if n < 2:
        return {}
    s = matrix @ matrix.T
    vals = s[np.triu_indices(n, k=1)]
    return {p: float(np.percentile(vals, p)) for p in (50, 90, 95, 99, 99.9)}
