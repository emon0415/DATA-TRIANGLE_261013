"""
隠れ出る杭検索：ナレッジ（373件）で検索と分析がうまく働くかを検証する

使い方
  pip install numpy pandas scikit-learn openai
  # 1) 課金なしの試運転（文字N-gramのTF-IDFで代用。閾値の絶対値は意味がないので分位で見る）
  python verify_analysis.py --backend tfidf
  # 2) 本番と同じモデル（text-embedding-3-small）。OPENAI_API_KEY が必要
  #    約1,650本×数百字で、費用は数円程度。結果は cache/ に保存し、2回目以降は課金なし
  python verify_analysis.py --backend openai

入力（同じフォルダに置く）
  db_load_373.json / eval_queries.json
出力
  画面に要約、reports/ に明細のCSV
"""
import argparse, json, math, os, re
from collections import Counter, defaultdict
import numpy as np
try:                                   # .env があれば OPENAI_API_KEY を読み込む（python-dotenv が必要）
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import pandas as pd

ROLE_W = {"提案者": 1.0, "主担当": 1.0, "副担当": 0.6, "責任者": 0.3}   # 引き継ぎ資料の仮の重み
PLANNED_W = 0.8                                                        # 計画中PJの割引（仮）
ANALYSIS = {
    "B": {"roles": {"行動案", "目的", "行動"}, "min_size": 2, "min_depts": None},  # 車輪の再発明
    "A": {"roles": {"課題意識"},             "min_size": 4, "min_depts": 2},     # 同じ問題意識
}

# ---------- データ ----------
def load(dir_):
    db = json.load(open(os.path.join(dir_, "db_load_373.json"), encoding="utf-8"))
    ev = json.load(open(os.path.join(dir_, "eval_queries.json"), encoding="utf-8"))
    docs = {d["doc_id"]: d for d in db["documents"]}
    secs = pd.DataFrame(db["document_sections"])
    auth = pd.DataFrame(db["document_authors"])
    # 文書の部門：提案は提案者の部門、PJは主管部門
    dept = {}
    for d in docs.values():
        if d["doc_type"] == "project":
            dept[d["doc_id"]] = d["owner_dept_id"]
    for r in auth[auth.role == "提案者"].itertuples():
        dept[r.doc_id] = r.dept_id_at_time
    secs["title"] = secs.doc_id.map(lambda i: docs[i]["title"])
    secs["text"] = "表題：" + secs.title + "／章：" + secs.section_name + "／本文：" + secs.body
    return docs, secs, auth, dept, ev

# ---------- 埋め込み ----------
def embed(texts, backend, cache_dir="cache"):
    os.makedirs(cache_dir, exist_ok=True)
    if backend == "tfidf":
        return None  # tfidf は別処理（クエリと同じ語彙で作るため）
    path = os.path.join(cache_dir, f"{backend}.json")
    cache = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    todo = [t for t in dict.fromkeys(texts) if t not in cache]
    if todo:
        from openai import OpenAI
        cli = OpenAI()
        for i in range(0, len(todo), 100):
            res = cli.embeddings.create(model="text-embedding-3-small", input=todo[i:i + 100])
            for t, e in zip(todo[i:i + 100], res.data):
                cache[t] = e.embedding
        json.dump(cache, open(path, "w", encoding="utf-8"))
    m = np.array([cache[t] for t in texts], dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)

class Vectorizer:
    """backend ごとに、章とクエリを同じ空間のベクトルにする"""
    def __init__(self, backend, sec_texts):
        self.backend = backend
        if backend == "tfidf":
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.tf = TfidfVectorizer(analyzer="char", ngram_range=(2, 3), sublinear_tf=True, max_features=20000)
            self.S = self._norm(self.tf.fit_transform(sec_texts).toarray().astype(np.float32))
        else:
            self.S = embed(sec_texts, backend)
    @staticmethod
    def _norm(m):
        n = np.linalg.norm(m, axis=1, keepdims=True); n[n == 0] = 1
        return m / n
    def query(self, q):
        if self.backend == "tfidf":
            return self._norm(self.tf.transform([q]).toarray().astype(np.float32))[0]
        return embed([q], self.backend)[0]

# ---------- BM25（文字2-gram。キーワード検索の基準線） ----------
class BM25:
    def __init__(self, texts, k1=1.5, b=0.75):
        self.docs = [self.tok(t) for t in texts]
        self.avg = np.mean([len(d) for d in self.docs]); self.k1, self.b = k1, b
        df = Counter(g for d in self.docs for g in set(d)); N = len(self.docs)
        self.idf = {g: math.log(1 + (N - n + 0.5) / (n + 0.5)) for g, n in df.items()}
        self.tf = [Counter(d) for d in self.docs]
    @staticmethod
    def tok(t):
        t = re.sub(r"\s+", "", t); return [t[i:i + 2] for i in range(len(t) - 1)]
    def scores(self, q):
        out = np.zeros(len(self.docs), dtype=np.float32)
        for g in set(self.tok(q)):
            if g not in self.idf: continue
            for i, tf in enumerate(self.tf):
                f = tf.get(g, 0)
                if f: out[i] += self.idf[g] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(self.docs[i]) / self.avg))
        return out

# ---------- 検索：章 → 文書 → 人（いずれも最大値） ----------
def rank(sec_scores, secs, docs, auth, searcher=None, beta=0.0):
    s = secs.assign(score=sec_scores)
    doc_s = s.groupby("doc_id").score.max()
    rows = []
    for r in auth.itertuples():
        w = ROLE_W[r.role] * (PLANNED_W if docs[r.doc_id].get("pj_status") == "計画中" else 1.0)
        rows.append((r.emp_no, r.doc_id, doc_s[r.doc_id] * w))
    p = pd.DataFrame(rows, columns=["emp_no", "doc_id", "score"])
    if searcher:
        p = p[p.emp_no != searcher]
    p = p.sort_values("score", ascending=False)
    best = p.drop_duplicates("emp_no").set_index("emp_no")                 # 人ごとに最も強い文書
    if beta:                                                               # 関連する経験が複数ある人を少し上げる
        second = p[p.duplicated("emp_no")].drop_duplicates("emp_no").set_index("emp_no").score
        best["score"] = best.score + beta * second.reindex(best.index).fillna(0)
    best = best.reset_index().sort_values("score", ascending=False)
    return doc_s.sort_values(ascending=False), best.reset_index(drop=True)

def metrics(order, expected, ks=(5, 10)):
    order = list(order); pos = [order.index(e) + 1 for e in expected if e in order]
    out = {f"R@{k}": (sum(p <= k for p in pos) / len(expected)) if expected else np.nan for k in ks}
    out["MRR"] = 1 / min(pos) if pos else 0.0
    out["順位"] = sorted(pos)
    return out

def build_kanban(vec, secs, docs, auth):
    """看板（仮）：その人の文書のベクトルを役割の重みで平均したもの。
    本番では文書から書いた看板の文章を埋め込むが、まずはこの近似で効果の有無を確かめる"""
    doc_ids = secs.doc_id.to_numpy(dtype=object)
    dvec = {}
    for d in docs:
        m = vec.S[doc_ids == d].mean(axis=0); dvec[d] = m / (np.linalg.norm(m) or 1)
    acc = defaultdict(lambda: 0); wsum = defaultdict(float)
    for r in auth.itertuples():
        w = ROLE_W[r.role] * (PLANNED_W if docs[r.doc_id].get("pj_status") == "計画中" else 1.0)
        acc[r.emp_no] = acc[r.emp_no] + w * dvec[r.doc_id]; wsum[r.emp_no] += w
    emps = sorted(acc)
    K = np.array([acc[e] / wsum[e] for e in emps], dtype=np.float32)
    K /= np.linalg.norm(K, axis=1, keepdims=True)
    return emps, K

def rank_with_kanban(ppl, qv, kanban, alpha):
    """人の点数 = α×文書の経路の点数 ＋（1−α）×看板との類似度"""
    emps, K = kanban
    ks = dict(zip(emps, K @ qv))
    p = ppl.copy()
    p["score"] = alpha * p.score + (1 - alpha) * p.emp_no.map(ks)
    return p.sort_values("score", ascending=False).reset_index(drop=True)

def eval_search(vec, bm, secs, docs, auth, ev, outdir, alphas=(0.8,), betas=(0.3, 0.5)):
    res, detail = [], []
    kanban = build_kanban(vec, secs, docs, auth)
    for q in ev["queries"]:
        exp_emp = q["expected_emp"] or sorted(set(auth[auth.doc_id.isin(q["expected_docs"])].emp_no))
        qv = vec.query(q["text"])
        runs = [("vector", vec.S @ qv, None), ("bm25", bm.scores(q["text"]), None)] + \
               [(f"vector+看板 α={a}", vec.S @ qv, a) for a in alphas] + \
               [(f"vector+2番目 β={b}", vec.S @ qv, ("beta", b)) for b in betas]
        for name, sc, alpha in runs:
            beta = alpha[1] if isinstance(alpha, tuple) else 0.0
            if isinstance(alpha, tuple): alpha = None
            doc_rank, ppl = rank(sc, secs, docs, auth, q["searcher_emp"], beta)
            if alpha is not None:
                ppl = rank_with_kanban(ppl, qv, kanban, alpha)
            dm = metrics(doc_rank.index, q["expected_docs"]) if q["expected_docs"] else {}
            pm = metrics(ppl.emp_no, exp_emp)
            neg = [list(doc_rank.index).index(n) + 1 for n in q["negative_docs"]]
            res.append({"query": q["id"], "method": name,
                        "文書R@10": dm.get("R@10"), "文書MRR": dm.get("MRR"), "正解文書の順位": dm.get("順位"),
                        "人R@5": pm["R@5"], "人R@10": pm["R@10"], "人MRR": pm["MRR"], "正解者の順位": pm["順位"],
                        "負例の順位(大きいほど良い)": neg,
                        "検索者の除外": q["searcher_emp"] not in set(ppl.emp_no) if q["searcher_emp"] else None})
            for i, r in ppl.head(10).iterrows():
                detail.append({"query": q["id"], "method": name, "rank": i + 1, "emp_no": r.emp_no,
                               "doc_id": r.doc_id, "score": round(float(r.score), 4), "正解": r.emp_no in exp_emp})
    pd.DataFrame(detail).to_csv(os.path.join(outdir, "search_top10.csv"), index=False, encoding="utf-8-sig")
    return pd.DataFrame(res)

def secs_score(vec, text):
    return vec.S @ vec.query(text)

# ---------- 類似度の分布（無関係な章どうしの基準） ----------
def sim_distribution(vec, secs):
    S = vec.S @ vec.S.T
    d = np.asarray(secs.doc_id.values, dtype=object)
    iu = np.triu_indices(len(d), 1)
    v = S[iu][d[iu[0]] != d[iu[1]]]
    return {p: float(np.percentile(v, p)) for p in (50, 90, 95, 99, 99.9)}, S

# ---------- クラスタリング（方式を比べる） ----------
def centered(V, n_pc=1):
    """全文書に共通する成分を除く：平均を引き、主成分の上位 n_pc 本を取り除いて正規化し直す"""
    X = V - V.mean(axis=0, keepdims=True)
    for _ in range(n_pc):                   # 最大の主成分をべき乗法で求めて取り除く（次元が大きくても軽い）
        v = np.random.default_rng(0).standard_normal(X.shape[1]).astype(X.dtype)
        for _ in range(50):
            v = X.T @ (X @ v); v /= np.linalg.norm(v)
        X = X - np.outer(X @ v, v)
    n = np.linalg.norm(X, axis=1, keepdims=True); n[n == 0] = 1
    return X / n

def link_groups(sub, doc_ids, method, th, k=3):
    """sub：対象章どうしの類似度行列。戻り値：章の添字のリストのリスト"""
    n = len(doc_ids)
    same = doc_ids[:, None] == doc_ids[None, :]
    if method in ("average", "complete"):
        from sklearn.cluster import AgglomerativeClustering
        D = 1 - sub.astype(np.float64)
        D[same] = 1.0                       # 同じ文書の章どうしは近いとみなさない（N3）
        np.fill_diagonal(D, 0)
        lab = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage=method,
                                      distance_threshold=1 - th).fit(D).labels_
    else:
        parent = list(range(n))
        def f(x):
            while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
            return x
        E = (sub >= th) & ~same
        np.fill_diagonal(E, False)
        if method == "mknn":                # 互いに相手が上位k件に入るときだけ繋ぐ
            s = np.where(same, -np.inf, sub); np.fill_diagonal(s, -np.inf)
            top = np.argsort(-s, axis=1)[:, :k]
            T = np.zeros_like(E); T[np.arange(n)[:, None], top] = True
            E &= T & T.T
        for i, j in zip(*np.where(np.triu(E, 1))):
            parent[f(i)] = f(j)
        lab = np.array([f(i) for i in range(n)])
    groups = defaultdict(list)
    for i, l in enumerate(lab): groups[l].append(i)
    return list(groups.values())

def eval_clusters(V, secs, dept, ev, outdir, abs_ths, pcts, methods, k, level="doc"):
    rows, members = [], []
    checks = ev["cluster_checks"]
    spaces = {"raw": V}                       # center は不採用（B1の提案2件が繋がらなかった）
    for kind, cfg in ANALYSIS.items():
        idx = np.where(secs.section_role.isin(cfg["roles"]).to_numpy())[0]
        sec_doc = np.asarray(secs.doc_id.values[idx], dtype=object)
        if level == "doc":
            doc_ids = np.array(sorted(set(sec_doc)), dtype=object)
            pos = {d: i for i, d in enumerate(doc_ids)}
            owner = np.array([pos[d] for d in sec_doc])
        else:
            doc_ids = sec_doc
        cross = doc_ids[:, None] != doc_ids[None, :]
        iu = np.triu_indices(len(doc_ids), 1)
        for sp, M in spaces.items():
            ss = M[idx] @ M[idx].T
            if level == "doc":              # 文書どうしの類似度 ＝ 対象の章どうしで最も似ている組の類似度
                sub = np.full((len(doc_ids), len(doc_ids)), -1.0, dtype=np.float32)
                for a in range(len(doc_ids)):
                    ra = owner == a
                    sub[a] = [ss[np.ix_(ra, owner == b)].max() for b in range(len(doc_ids))]
            else:
                sub = ss
            vals = sub[iu][cross[iu]]
            # 閾値：raw は絶対値（指定があれば）と分位、center は分位のみ
            ths = [(f"{t:.2f}", t) for t in (abs_ths if sp == "raw" else [])] + \
                  [(f"p{p}", float(np.percentile(vals, p))) for p in pcts]
            for method in methods:
                for tname, th in ths:
                    groups = link_groups(sub, doc_ids, method, th, k)
                    valid, doc2c = [], {}
                    for g in groups:
                        ds = set(doc_ids[g])
                        if len(ds) < cfg["min_size"]: continue
                        nd = len({dept.get(x) for x in ds})
                        if cfg["min_depts"] and nd < cfg["min_depts"]: continue
                        m = sub[np.ix_(g, g)].copy(); m[~cross[np.ix_(g, g)]] = np.inf
                        cid = len(valid)
                        valid.append({"size": len(ds), "min_sim": float(m.min())})
                        for x in ds: doc2c[x] = (cid, len(ds))
                        members += [{"analysis": kind, "space": sp, "method": method, "threshold": tname,
                                     "cluster": cid, "doc_id": x, "size": len(ds)} for x in sorted(ds)]
                    sizes = sorted((v["size"] for v in valid), reverse=True)
                    def linked(grp):
                        c = {doc2c.get(x, (None,))[0] for x in grp}
                        return len(c) == 1 and None not in c
                    mk = checks.get(f"must_link_{kind}", []); mn = checks.get(f"must_not_link_{kind}", [])
                    rows.append({
                        "分析": kind, "空間": sp, "方式": method, "閾値": tname, "閾値の値": round(th, 3),
                        "塊の数": len(valid), "最大の塊": sizes[0] if sizes else 0, "上位5": sizes[:5],
                        "塊に入った文書の割合": round(len(doc2c) / len(set(doc_ids)), 3),
                        "最低類似度の最小": round(min((v["min_sim"] for v in valid), default=np.nan), 3),
                        "繋がるべき組（成立数/組数）": f"{sum(linked(g) for g in mk)}/{len(mk)}",
                        "その塊の大きさ": [doc2c[g[0]][1] if linked(g) else "-" for g in mk],
                        "誤結合": f"{sum(linked(g) for g in mn)}/{len(mn)}",
                    })
    pd.DataFrame(members).to_csv(os.path.join(outdir, "cluster_members.csv"), index=False, encoding="utf-8-sig")
    return pd.DataFrame(rows)

def pair_sims(S, secs, groups, roles):
    """確認したい組の、章どうしの最大類似度（閾値との距離を見る）"""
    out = []
    for grp in groups:
        for a in range(len(grp)):
            for b in range(a + 1, len(grp)):
                ia = np.where((secs.doc_id == grp[a]) & secs.section_role.isin(roles))[0]
                ib = np.where((secs.doc_id == grp[b]) & secs.section_role.isin(roles))[0]
                if len(ia) and len(ib):
                    out.append({"組": f"{grp[a]} × {grp[b]}", "最大類似度": round(float(S[np.ix_(ia, ib)].max()), 3)})
    return pd.DataFrame(out)

# ---------- 実行 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["tfidf", "openai"], required=True)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--thresholds", default="0.60,0.65,0.70",
                    help="openai・rawの空間で使う絶対値。分位（p99〜p99.9）は常に併せて振る")
    ap.add_argument("--methods", default="average,single", help="採用は average。比較用に single も出す")
    ap.add_argument("--k", type=int, default=3, help="mknnの上位k件")
    a = ap.parse_args()
    outdir = os.path.join(a.dir, "reports", a.backend); os.makedirs(os.path.join(outdir, "sec"), exist_ok=True)
    pd.set_option("display.width", 200); pd.set_option("display.max_columns", 20)

    docs, secs, auth, dept, ev = load(a.dir)
    print(f"文書 {len(docs)} 件／章 {len(secs)} 本／著者 {len(auth)} 行")
    vec = Vectorizer(a.backend, secs.text.tolist())
    bm = BM25(secs.text.tolist())

    print("\n■1 無関係な章どうしの類似度の分布（閾値の基準。実測では中央0.45／上位1%で0.65）")
    dist, S = sim_distribution(vec, secs)  # S は ■5 で使う
    print({f"p{k}": round(v, 3) for k, v in dist.items()})

    print("\n■2 検索（ベクトル vs キーワードBM25）")
    sr = eval_search(vec, bm, secs, docs, auth, ev, outdir)
    print(sr.to_string(index=False))
    print("平均：\n", sr.groupby("method", sort=False)[["文書R@10", "文書MRR", "人R@5", "人R@10", "人MRR"]].mean().round(3).to_string())
    sr.to_csv(os.path.join(outdir, "search_metrics.csv"), index=False, encoding="utf-8-sig")

    print("\n■3 キーワードの罠：「予兆」で検索したときの上位5件（BM25）")
    doc_rank, _ = rank(bm.scores("予兆"), secs, docs, auth)
    print([f"{d}:{docs[d]['title'][:18]}" for d in doc_rank.index[:5]])

    abs_ths = [float(x) for x in a.thresholds.split(",")] if a.backend == "openai" else []
    print("\n■4 クラスタ：方式（single=単連結／average=平均連結／complete=完全連結／mknn=相互上位k件）"
          "×空間（raw=そのまま／center=共通成分を除去）×閾値")
    cr = pd.concat([eval_clusters(vec.S, secs, dept, ev, outdir if lv == "doc" else os.path.join(outdir, "sec"),
                                  abs_ths, [99, 99.5], a.methods.split(","), a.k, lv).assign(単位=lv)
                    for lv in ("doc", "section")], ignore_index=True)
    print(cr.to_string(index=False))
    cr.to_csv(os.path.join(outdir, "cluster_summary.csv"), index=False, encoding="utf-8-sig")

    print("\n■5 確認したい組の類似度（分析Bの対象章）")
    ch = ev["cluster_checks"]
    pb = pair_sims(S, secs, ch["must_link_B"] + ch["must_not_link_B"], ANALYSIS["B"]["roles"])
    print(pb.to_string(index=False)); pb.assign(分析="B").to_csv(os.path.join(outdir, "pair_sims.csv"), index=False, encoding="utf-8-sig")
    print("（分析Aの対象章）")
    print(pair_sims(S, secs, ch["must_link_A"] + ch["must_not_link_A"], ANALYSIS["A"]["roles"]).to_string(index=False))
    print(f"\n明細は {outdir}/ に出力しました")

if __name__ == "__main__":
    main()
