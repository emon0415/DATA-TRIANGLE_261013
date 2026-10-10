# =============================================================
# themes.py — 組織のナレッジの偏りを見る：「属人化マップ」と「空白地帯マップ」の計算
#
#   load(sb, ctx)               … 文書のベクトル・テーマ分け・2次元の地図の座標を作る（起動時に1回。画面側でキャッシュする）
#   unique_docs(th, cut)        … 「他の人の文書に、似た経験が見当たらない」文書を選ぶ（属人化マップ）
#   person_table(th, ctx, cut)  … その文書を多く持っている人の一覧（在職・異動も添える）
#   successors(th, ctx, emp)    … その人の経験に近い、ほかの人（引き継ぎの候補）
#   gaps(th, ctx, ...)          … テーマごとの「解決した件数」「未解決の件数」（空白地帯マップ）
#
# 考え方
#   ・文書のベクトル ＝ 章のベクトル（DBの埋め込み）の平均。看板（非公開メモ）は使わない
#   ・属人化：文書ごとに、「同じ人が関わっていない文書」のうち、最も近いものとの類似度を見る。
#       その値が全文書の下位 cut（既定10%）にある文書＝似た経験を持つ人が、ほかに見当たらない
#       一人の人の文書が少ない会社では、テーマ単位で「関わる人が少ない」領域は出にくいので、文書単位で見る
#   ・空白地帯：文書をテーマ（k-means）に分け、テーマごとに「解決」と「未解決」を数える
#       解決＝採択された提案・完了したPJ（検索の「実績のある人」と同じ）
#       未解決＝アイデア投稿、不採択・保留の提案、中止したPJ（「原石の人」の元になる文書）
#       進行中・計画中・審査中は「取り組み中」で、どちらにも数えない
#   ・どちらも、文書の数が増えれば結果が変わる。基準（下位何％、何件以上）は画面で動かせる
# =============================================================
import re
from collections import Counter, defaultdict

import numpy as np

from search import network, rank

THEME_K = 80                      # テーマの数（文書が1,500件ほどで、1テーマ20件前後）
SOLVED, OPEN, UNSOLVED = "解決", "取り組み中", "未解決"
DEFAULTS = {"cut": 0.10, "min_docs": 8, "max_rate": 0.15}


def _fetch(sb, table, cols, order, page=1000):
    return network._fetch(sb, table, cols, order, page)


def status(doc):
    """文書の状態：解決／取り組み中／未解決"""
    if rank._is_proven(doc):
        return SOLVED
    if doc["doc_type"] == "project" and doc["pj_status"] in ("進行中", "計画中"):
        return OPEN
    if doc["doc_type"] == "proposal" and doc["result"] == "審査中":
        return OPEN
    return UNSOLVED


def kmeans(v, k, seed=0, iters=40):
    """向きで分ける k-means（ベクトルは長さ1。乱数のシードを固定して、毎回同じ結果にする）"""
    k = min(k, len(v))
    rng = np.random.default_rng(seed)
    cent = [v[rng.integers(len(v))]]
    for _ in range(k - 1):                       # k-means++：離れた点を、次の中心の候補にする
        d = (1 - np.max(v @ np.array(cent).T, axis=1)).clip(min=0) ** 2
        cent.append(v[rng.choice(len(v), p=d / d.sum())] if d.sum() > 0 else v[rng.integers(len(v))])
    cent = np.array(cent)
    for _ in range(iters):
        lab = np.argmax(v @ cent.T, axis=1)
        for j in range(k):
            m = v[lab == j]
            if len(m):
                c = m.mean(0)
                cent[j] = c / (np.linalg.norm(c) or 1)
    return np.argmax(v @ cent.T, axis=1)


def theme_names(lab, doc_ids, kw, k, top=3):
    """テーマの名前：そのテーマに多く出て、ほかのテーマにはあまり出ないキーワード上位（c-TF-IDF）"""
    tf = [Counter() for _ in range(k)]
    for i, d in enumerate(doc_ids):
        tf[lab[i]].update(kw.get(d, {}))
    df = Counter(t for c in tf for t in c)
    names = []
    for c in tf:
        sc = {t: n * np.log((k + 1) / (df[t] + 0.5)) for t, n in c.items()}
        names.append("・".join(t for t, _ in sorted(sc.items(), key=lambda x: -x[1])[:top]) or "（その他）")
    return names


def load(sb, ctx, k=THEME_K):
    """文書ごとのベクトル・テーマ・地図の座標を作る。ベクトルは ctx（service.load）の章のベクトルから作るので、DBの読み込みは軽い"""
    secs, mat = ctx["sections"], ctx["matrix"]
    rows = defaultdict(list)
    for i, d in enumerate(secs["doc_id"].tolist()):
        rows[d].append(i)
    doc_ids = sorted(d for d in rows if d in ctx["docs"])
    v = np.array([mat[rows[d]].mean(0) for d in doc_ids], dtype=np.float32)
    v /= np.where((n := np.linalg.norm(v, axis=1, keepdims=True)) == 0, 1, n)

    auth = defaultdict(dict)                                   # 文書 → {社員: 文書を書いた時点の部署}
    for r in _fetch(sb, "document_authors", "doc_id,emp_id,dept_id_at_time", ["doc_id", "emp_id"]):
        auth[r["doc_id"]][r["emp_id"]] = r.get("dept_id_at_time")
    kw = defaultdict(dict)
    for r in _fetch(sb, "document_keywords", "doc_id,keyword,count", ["doc_id", "keyword"]):
        kw[r["doc_id"]][r["keyword"]] = r["count"]
    stop = network.name_stopwords(sb)
    kw = {d: {t: c for t, c in m.items() if t not in stop} for d, m in kw.items()}

    k = max(1, min(k, len(doc_ids) // 5)) if doc_ids else 1
    lab = kmeans(v, k) if len(doc_ids) else np.zeros(0, dtype=int)
    names = theme_names(lab, doc_ids, kw, k)

    # 近さ：同じ人が関わっている文書の組は除く。「他の人の文書」との、最大の類似度が、文書ごとの近さ
    people = sorted({e for a in auth.values() for e in a})
    pidx = {e: i for i, e in enumerate(people)}
    m = np.zeros((len(doc_ids), max(len(people), 1)), dtype=np.float32)
    for i, d in enumerate(doc_ids):
        for e in auth.get(d, {}):
            m[i, pidx[e]] = 1
    sim = v @ v.T
    sim[(m @ m.T) > 0] = -1
    nn = sim.max(1) if len(doc_ids) else np.zeros(0)
    order = nn.argsort()
    pct = np.empty(len(nn))
    pct[order] = np.arange(len(nn)) / max(len(nn), 1)          # 0＝他に似た文書が最もない、1＝最もある

    c = v - v.mean(0)                                          # 地図の座標（主成分分析の上位2軸）
    xy = (np.linalg.svd(c, full_matrices=False)[0][:, :2] * 1.0) if len(doc_ids) > 2 else np.zeros((len(doc_ids), 2))
    return {"doc_ids": doc_ids, "index": {d: i for i, d in enumerate(doc_ids)}, "v": v, "sim": sim, "nn": nn, "pct": pct,
            "xy": xy, "lab": lab, "names": names, "auth": dict(auth)}


def unique_docs(th, cut=DEFAULTS["cut"]):
    """他の人の文書に似た経験が見当たらない文書の、位置（行番号）のリスト"""
    return [i for i in range(len(th["doc_ids"])) if th["pct"][i] < cut]


def person_state(ctx, emp):
    """在職か、異動したか（文書を書いた時点の部署が、いまの部署と違う文書があるか）"""
    e = ctx["emps"][emp]
    return {"active": e.get("is_active", True)}


def moved(th, ctx, emp):
    now = ctx["emps"][emp]["dept_id"]
    return any(th["auth"].get(d, {}).get(emp) not in (None, now) for d, _ in ctx["person_docs"].get(emp, []))


def person_table(th, ctx, cut=DEFAULTS["cut"]):
    """属人化している文書を多く持つ人の一覧。戻り値：[{"emp", "unique", "total", "active", "moved"}]（多い順）"""
    uniq = Counter()
    for i in unique_docs(th, cut):
        for e in th["auth"].get(th["doc_ids"][i], {}):
            uniq[e] += 1
    total = {e: len(v) for e, v in ctx["person_docs"].items()}
    rows = [{"emp": e, "unique": n, "total": total.get(e, n), "active": ctx["emps"][e].get("is_active", True),
             "moved": moved(th, ctx, e)} for e, n in uniq.items()]
    return sorted(rows, key=lambda r: (-r["unique"], r["total"], r["emp"]))


def successors(th, ctx, emp, cut=DEFAULTS["cut"], top=3):
    """emp の「似た経験が他にない文書」に、最も近い文書を持つ、ほかの在職の人（引き継ぎの候補）。
    戻り値：[{"emp", "sim", "doc", "mine"}]（類似度の高い順。同じ人は1回だけ）"""
    mine = [th["index"][d] for d, _ in ctx["person_docs"].get(emp, []) if d in th["index"]]
    best = {}
    for i in mine:
        row = th["sim"][i]
        for j in np.argsort(-row)[:30]:
            if row[j] <= 0:
                break
            for e in th["auth"].get(th["doc_ids"][j], {}):
                if e == emp or not ctx["emps"].get(e, {}).get("is_active", True):
                    continue
                if e not in best or row[j] > best[e]["sim"]:
                    best[e] = {"emp": e, "sim": float(row[j]), "doc": th["doc_ids"][j], "mine": th["doc_ids"][i]}
    return sorted(best.values(), key=lambda r: -r["sim"])[:top]


def gaps(th, ctx, min_docs=DEFAULTS["min_docs"], max_rate=DEFAULTS["max_rate"]):
    """テーマごとの集計。戻り値：[{"theme", "name", "n", "solved", "open", "unsolved", "rate", "gap", "docs"}]
    gap＝空白地帯か（文書が min_docs 件以上で、解決の割合が max_rate 以下）。未解決の多い順"""
    by = defaultdict(list)
    for i, d in enumerate(th["doc_ids"]):
        by[int(th["lab"][i])].append(d)
    out = []
    for t, ds in by.items():
        st = Counter(status(ctx["docs"][d]) for d in ds)
        n = len(ds)
        rate = st[SOLVED] / n
        out.append({"theme": t, "name": th["names"][t], "n": n, "solved": st[SOLVED], "open": st[OPEN], "unsolved": st[UNSOLVED],
                    "rate": rate, "gap": n >= min_docs and rate <= max_rate, "docs": ds})
    return sorted(out, key=lambda r: (-r["gap"], -r["unsolved"], r["rate"]))


def dept_func(ctx, net, emp):
    """人の今の部署の職能名（品質保証、製造、営業 など）。空白地帯が、どの職能から出ているかを見るため"""
    return net["dept"][ctx["emps"][emp]["dept_id"]]["func"]
