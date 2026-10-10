# =============================================================
# network.py — 「つながりマップ」：ある人の周りの、つながりの強い人を選び、図にする
#
#   load(sb, ctx, profile)                 … 人ごとのベクトルを作る（起動時に1回。画面側でキャッシュする）。profile＝看板のベクトル（similar.load_vectors の戻り値）
#   neighbors(net, ctx, emp, ...)          … 1人を選んで、つながりの強い人を返す（「仲間を探す」の一覧も、これを使う）
#   build_html(ctx, net, emp, nbrs, ...)   … pyvis でグラフのHTMLを作る（物理シミュレーションで配置する）
#
# つながりの強さ ＝ 重み1 × 看板の近さ ＋ 重み2 × テーマの近さ ＋ 重み3 × 文章の近さ ＋ 重み4 × 知っている度
#   看板の近さ   ＝ 看板（ノートとキャリアシートを合わせた紹介文）の埋め込みベクトルのコサイン類似度。全員が高めに出るので、
#                  候補の中央値を0、最大を1にそろえる。文書がなく、キャリアシートだけの人も、これで出せる
#   テーマの近さ ＝ 人ごとのキーワード（文書のキーワード、出現文書が少ない語ほど重い）のコサイン類似度
#   文章の近さ   ＝ 人ごとの文章中の語（body_tokens）のコサイン類似度
#   知っている度 ＝ 組織の段階（STRENGTH）。重み4 がマイナスなら、知っている人ほど下がる（＝新しい出会いを優先）
#     段階：共同作業（同じ文書に関わった）＞同じ課＞同じ拠点＞同じ職能＞同じ区分＞別の区分
#     「知っている範囲」で選んだ段階までを知っているとみなし、それより外は 0。共同作業は常に知っているとみなす
#   本社の部は、別の部同士でも「同じ拠点」とはみなさない（同じ区分）。同じ職能（品質保証 など）は、本社と工場でも一致する
#   意外なつながり ＝ 看板は近い（相対で0.6以上）のに、文書のテーマは遠い（0.15未満）人。偶然の出会いの目印にする。
#                  「知っている範囲」の中の人（すでに知っている人）は、対象にしない
#
# 看板は、近さの点数にだけ使う。看板の中身（語や文）は、画面に出さない。
# 「共通する語」は、公開された文書のキーワードに限る（看板だけで近い人は、理由に語が出ない）。
# =============================================================
import json
import math
import re
from collections import defaultdict

import numpy as np

LEVEL_LABELS = ["共同作業", "同じ課", "同じ拠点", "同じ職能", "同じ区分", "別の区分"]
STRENGTH = [1.0, 0.8, 0.5, 0.3, 0.1, 0.0]        # 段階ごとの「知っている度」
RANGE_OPTIONS = {"同じ課まで": 1, "同じ拠点まで": 2, "同じ職能まで": 3, "同じ区分まで": 4}
DEFAULTS = {"t": 0.5, "range": "同じ拠点まで", "top_n": 10, "pair_min": 0.25}
# 「人を探す」「属人化マップ」は、事実（文書・組織）だけで描く。看板は使わず、設定も出さずに固定値を使う
FACT = {"人を探す": {"t": 0.0, "range": 1, "top_n": 10}, "属人化": {"t": 0.0, "range": 0, "top_n": 10}}   # range：1＝同じ課まで／0＝共同作業のみ
SURPRISE = {"prof_min": 0.6, "theme_max": 0.15}   # 意外なつながりの基準（看板の近さ ≥ prof_min、テーマの近さ < theme_max）
PROF_COLOR, DOC_COLOR = "#7B6AA8", "#8FA3B3"      # 線の色：看板が主な理由／文書が主な理由
LEVEL_COLORS = ["#3E5C76", "#6C8EAD", "#A9BFD1", "#E3B04B", "#EBC97E", "#D9822B"]   # 手前（知っている）が青、新しいほど暖色


def _fetch(sb, table, cols, order, page=1000):
    out, start = [], 0
    while True:
        q = sb.table(table).select(cols)
        for c in order:
            q = q.order(c)
        data = q.range(start, start + page - 1).execute().data
        out += data
        if len(data) < page:
            return out
        start += page


def _dept_parts(name, dept_type):
    """部署名を（拠点, 職能）に分ける。本社の部は拠点なし。例：第四工場 品質保証課 → (第四工場, 品質保証)"""
    if dept_type == "本社" or " " not in name:
        return (None if dept_type == "本社" else name), re.sub("部$", "", name)
    site, func = name.split(" ", 1)
    return site, re.sub("課$", "", func)


def _matrix(person_ids, doc_terms, docs_of, df_n):
    """人 × 語 の TF-IDF 行列（行は長さ1に正規化）。doc_terms: {文書ID: Counter}、docs_of: {社員ID: [文書ID]}"""
    df = defaultdict(int)
    for terms in doc_terms.values():
        for t in terms:
            df[t] += 1
    vocab = {t: i for i, t in enumerate(sorted(df))}
    idf = {t: math.log((df_n + 1) / (c + 1)) + 1 for t, c in df.items()}
    m = np.zeros((len(person_ids), max(len(vocab), 1)), dtype=np.float32)
    for r, e in enumerate(person_ids):
        for d in docs_of.get(e, []):
            for t, c in doc_terms.get(d, {}).items():
                m[r, vocab[t]] += (1 + math.log(c)) * idf[t]
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(n == 0, 1, n), vocab


def dept_info(sb):
    """部署ごとの {区分, 拠点, 職能, 名前}。部署マスタには親子の列がないので、名前から分ける"""
    info = {}
    for d in _fetch(sb, "departments", "dept_id,dept_name,dept_type", ["dept_id"]):
        site, func = _dept_parts(d["dept_name"], d["dept_type"])
        info[d["dept_id"]] = {"type": d["dept_type"], "site": site, "func": func, "name": d["dept_name"]}
    return info


def stopwords(info):
    """部署名・拠点名・職能名そのものは、共通の語に出さない（同じ部署というだけでつながるのを防ぐ）"""
    stop = {"工場", "支店", "営業所", "本社"}
    for v in info.values():
        stop |= {v["name"], v["name"].replace(" ", ""), v["func"], v["func"] + "部", v["func"] + "課"}
        if v["site"]:
            stop.add(v["site"])
    return stop


def name_stopwords(sb):
    return stopwords(dept_info(sb))


def load(sb, ctx, profile=None):
    """人ごとのベクトルと、部署の情報を作る。対象は、文書に関わった（著者になった）在職の人と、看板のある在職の人。
    profile：(社員IDのリスト, 看板の埋め込み行列)。similar.load_vectors の戻り値。なければ、看板の近さは0になる"""
    info = dept_info(sb)
    stop = stopwords(info)
    active = lambda e: ctx["emps"].get(e, {}).get("is_active", True)

    docs_of = {e: [d for d, _ in lst] for e, lst in ctx["person_docs"].items() if lst and active(e)}
    pids, pmat = profile if profile is not None else ([], None)
    people = sorted(set(docs_of) | {e for e in pids if e in ctx["emps"] and active(e)})
    kw = defaultdict(dict)
    for r in _fetch(sb, "document_keywords", "doc_id,keyword,count", ["doc_id", "keyword"]):
        if r["keyword"] not in stop:
            kw[r["doc_id"]][r["keyword"]] = r["count"]
    text = defaultdict(lambda: defaultdict(int))
    for r in _fetch(sb, "document_sections", "section_id,doc_id,body_tokens", ["section_id"]):
        for t in (r.get("body_tokens") or "").split():
            if t not in stop:
                text[r["doc_id"]][t] += 1
    n_docs = len(ctx["docs"])
    mk, vk = _matrix(people, kw, docs_of, n_docs)
    mt, _ = _matrix(people, text, docs_of, n_docs)
    mp = np.zeros((len(people), pmat.shape[1] if pmat is not None and len(pids) else 1), dtype=np.float32)
    has_p = np.zeros(len(people), dtype=bool)
    where = {e: i for i, e in enumerate(pids)}
    for r, e in enumerate(people):
        if e in where:
            mp[r], has_p[r] = pmat[where[e]], True
    return {"people": people, "index": {e: i for i, e in enumerate(people)}, "mk": mk, "mt": mt, "mp": mp, "has_p": has_p,
            "vocab_k": [t for t, _ in sorted(vk.items(), key=lambda x: x[1])],
            "docs_of": {e: set(docs_of.get(e, [])) for e in people}, "dept": info}


def level(net, ctx, a, b):
    """aから見たbの組織の段階（0〜5）"""
    if net["docs_of"].get(a, set()) & net["docs_of"].get(b, set()):
        return 0
    da, db = ctx["emps"][a]["dept_id"], ctx["emps"][b]["dept_id"]
    if da == db:
        return 1
    ia, ib = net["dept"][da], net["dept"][db]
    if ia["site"] and ia["site"] == ib["site"]:
        return 2
    if ia["func"] == ib["func"]:
        return 3
    return 4 if ia["type"] == ib["type"] else 5


def weights(t):
    """スライダー t（0＝すでに強い関係、1＝新しい出会い）から、(看板, テーマ, 文章, 知っている度) の重みを作る。
    中央（t=0.5）は 0.35／0.20／0.15／0。知っている度は、左端 +0.30、右端 −0.30"""
    return 0.30 + 0.10 * t, 0.175 + 0.05 * t, 0.125 + 0.05 * t, 0.30 - 0.60 * t


def neighbors(net, ctx, emp, t=DEFAULTS["t"], known_range=2, top_n=DEFAULTS["top_n"], use_profile=True):
    """emp とつながりの強い人を上位 top_n 人返す。文書も看板もない人は status="no_docs"。
    戻り値：{"status", "people": [{"emp", "score", "prof", "theme", "text", "level", "shared_docs", "words", "parts", "surprise"}]}
    parts＝(看板, テーマ, 文章, 知っている度) の、重みを掛けたあとの点
    use_profile=False のときは、看板を使わない（看板の点は0、残りの重みを比例配分。◆も出さない）"""
    if emp not in net["index"]:
        return {"status": "no_docs", "people": []}
    i = net["index"][emp]
    th, tx = net["mk"] @ net["mk"][i], net["mt"] @ net["mt"][i]
    th[i] = tx[i] = 0
    has_p = net["has_p"]
    pr = (net["mp"] @ net["mp"][i]) if has_p[i] else np.zeros(len(net["people"]), dtype=np.float32)
    pr = np.where(has_p, pr, 0.0)
    pr[i] = 0
    cand = has_p.copy()
    cand[i] = False
    if has_p[i] and cand.any():                  # 看板の近さ：候補の中央値を0、最大を1にそろえる
        base, top = float(np.median(pr[cand])), float(pr[cand].max())
        pr_rel = np.where(cand, np.clip((pr - base) / max(top - base, 1e-9), 0, 1), 0.0)
    else:
        pr_rel = np.zeros(len(net["people"]))
    w0, w1, w2, w3 = weights(t)
    if not use_profile:
        pr_rel = np.zeros(len(net["people"]))
        k = 1.0 / (1.0 - w0)
        w0, w1, w2, w3 = 0.0, w1 * k, w2 * k, w3 * k
    mt_, mx_ = max(th.max(), 1e-9), max(tx.max(), 1e-9)
    rows = []
    for j, e in enumerate(net["people"]):
        if j == i:
            continue
        lv = level(net, ctx, emp, e)
        known = STRENGTH[lv] if lv <= known_range or lv == 0 else 0.0
        parts = tuple(float(x) for x in (w0 * pr_rel[j], w1 * th[j] / mt_, w2 * tx[j] / mx_, w3 * known))   # numpy の数値のままだと、グラフ(JSON)に渡せない
        rows.append({"emp": e, "score": sum(parts), "prof": float(pr[j]), "theme": float(th[j]), "text": float(tx[j]), "level": lv,
                     "parts": parts, "shared_docs": sorted(net["docs_of"][emp] & net["docs_of"][e]),
                     "surprise": bool(use_profile and pr_rel[j] >= SURPRISE["prof_min"] and th[j] / mt_ < SURPRISE["theme_max"] and lv > known_range)})
    rows.sort(key=lambda r: -r["score"])
    rows = rows[:top_n]
    for r in rows:
        j = net["index"][r["emp"]]
        both = np.minimum(net["mk"][i], net["mk"][j])
        r["words"] = [net["vocab_k"][k] for k in np.argsort(-both)[:3] if both[k] > 0]     # 公開された文書のキーワードだけ。看板の語は出さない
    return {"status": "ok", "people": rows}


def person_summary(net, ctx, emp, shared=(), n_docs=None, n_words=5):
    """つながりマップのカードに出す、その人のナレッジの要約（クリックなしで見える）。看板は使わない（公開された文書だけ）。
    戻り値：{"words": 得意なテーマの語, "counts": {"完了PJ", "採択提案", "ほか"}, "total": 文書数, "docs": 紐づく文書（並び順どおり）}
    文書の並び：中心の人と共同作業した文書 → 実績のある文書（完了PJ・採択提案）→ 新しい順"""
    from search import rank, profiles as P
    words = []
    if emp in net["index"]:
        row = net["mk"][net["index"][emp]]
        words = [net["vocab_k"][k] for k in np.argsort(-row)[:n_words] if row[k] > 0]
    docs = []
    for doc_id, role in (ctx.get("person_docs") or {}).get(emp, []):
        d = ctx["docs"].get(doc_id)
        if d:
            docs.append((doc_id, role, d))
    counts = {"完了PJ": 0, "採択提案": 0, "ほか": 0}
    for _, _, d in docs:
        if rank._is_proven(d):
            counts["完了PJ" if d["doc_type"] == "project" else "採択提案"] += 1
        else:
            counts["ほか"] += 1
    shared = set(shared)
    docs.sort(key=lambda x: (x[0] in shared, rank._is_proven(x[2]), P.doc_date(x[2])), reverse=True)
    top = [{"doc_id": i, "code": d["doc_code"], "title": d["title"], "label": P.doc_label(d), "icon": P.doc_icon(d),
            "role": role, "shared": i in shared, "proven": rank._is_proven(d)} for i, role, d in docs[:n_docs]]       # n_docs=None なら全件
    return {"words": words, "counts": counts, "total": len(docs), "docs": top}


def _py(x):
    """numpy の整数を、ふつうの整数にする（グラフに渡すIDはJSONにできる型でなければならない）"""
    return x.item() if hasattr(x, "item") else x


def build_html(ctx, net, emp, nbrs, height=560, pair_min=DEFAULTS["pair_min"]):
    """pyvis でグラフのHTMLを作る。線の長さ・太さはつながりの強さ、ノードの色は組織の段階。
    配置は物理シミュレーション（強い人ほど近くに寄る）。乱数のシードを固定して、動かしても形が大きく崩れないようにする"""
    from pyvis.network import Network
    g = Network(height=f"{height}px", width="100%", bgcolor="#ffffff", font_color="#222222", cdn_resources="in_line")
    g.set_options("""{"layout": {"randomSeed": 7},
      "physics": {"solver": "forceAtlas2Based",
                  "forceAtlas2Based": {"gravitationalConstant": -70, "springConstant": 0.08, "centralGravity": 0.01},
                  "stabilization": {"iterations": 250}},
      "interaction": {"hover": true, "tooltipDelay": 80},
      "nodes": {"font": {"size": 15}}}""")
    name = lambda e: ctx["emps"][e]["name"]
    dept = lambda e: ctx["dept_name"].get(ctx["emps"][e]["dept_id"], "")
    g.add_node(emp, label=name(emp), title=f"{name(emp)}\n{dept(emp)}\n（この人を中心に表示）", size=36, color="#222222",
               font={"color": "#222222", "size": 17, "bold": True})
    top = max((r["score"] for r in nbrs), default=1.0) or 1.0
    emp = _py(emp)
    for r in nbrs:
        e = _py(r["emp"])
        rel = max(r["score"], 0) / top
        shared = [ctx["docs"][d]["title"] for d in r["shared_docs"] if d in ctx["docs"]]
        a, b, c, d = r["parts"]
        main = max(range(3), key=lambda k: r["parts"][k]) if a > 0 else 1 + max(range(2), key=lambda k: r["parts"][1 + k])   # 点が最も大きい項目：0＝看板、1＝テーマ、2＝文章
        tip = [name(e), dept(e), f"組織の段階：{LEVEL_LABELS[r['level']]}",
               f"つながりの強さ：{r['score']:.2f}（看板 {a:.2f}／テーマ {b:.2f}／文章 {c:.2f}／知っている度 {d + 0.0:+.2f}）"]
        if r["surprise"]:
            tip.append("◆ 意外なつながり：関心（看板）は近いが、文書では接点が薄い")
        if r["words"]:
            tip.append("共通する語：" + "、".join(r["words"]))
        elif a > 0 and main == 0:
            tip.append("関心が近い（内容は表示しません）")
        if shared:
            tip.append("共同作業：" + " / ".join(s[:24] for s in shared[:2]))
        g.add_node(e, label=name(e), title="\n".join(tip), size=14 + 18 * rel, color=LEVEL_COLORS[r["level"]],
                   shape="diamond" if r["surprise"] else "dot")
        g.add_edge(emp, e, width=1 + 6 * rel, length=300 - 170 * rel, dashes=r["level"] != 0,
                   color="#3E5C76" if r["level"] == 0 else (PROF_COLOR if main == 0 else DOC_COLOR),
                   title=(f"共通する語：{'、'.join(r['words'])}" if r["words"] else ("関心が近い" if main == 0 else "")))
    ids = [_py(r["emp"]) for r in nbrs]
    for a in range(len(ids)):                   # 周りの人どうしも、関心が近ければ細い線でつなぐ（かたまりが見える）
        for b in range(a + 1, len(ids)):
            c = float(net["mk"][net["index"][ids[a]]] @ net["mk"][net["index"][ids[b]]])
            if c >= pair_min:
                g.add_edge(ids[a], ids[b], width=1, color="#DDE3E8", length=220, title=f"テーマの近さ {c:.2f}")
    html = g.generate_html()
    # 丸をクリックしたら、親の画面（Streamlit）で、その人のカード（id="mp-社員ID"）まで、なめらかに動かす。
    # 親の画面に触れられない環境（別のオリジンなど）では、何も起きない
    js = """<script>
(function () {
  var CENTER = %s;
  function go(id) {
    try {
      var el = window.parent.document.getElementById('mp-' + id);
      if (!el) return;
      el.scrollIntoView({behavior: 'smooth', block: 'start'});
      el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    } catch (e) {}
  }
  if (typeof network !== 'undefined') {
    network.on('click', function (p) { if (p.nodes.length && p.nodes[0] !== CENTER) go(p.nodes[0]); });
  }
})();
</script>
</body>""" % json.dumps(emp)
    return html[:html.rindex("</body>")] + js + html[html.rindex("</body>") + len("</body>"):]
