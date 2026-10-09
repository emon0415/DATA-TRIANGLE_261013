# =============================================================
# service.py — 本番の「人を探す」画面（app.py）から呼ぶ、検索のまとめ役
#
#   load(sb)     … 順位づけに必要なデータをDBから読む（起動時に1回。画面側でキャッシュする）
#   search(...)  … 質問文から、2つの枠（実績のある人／隠れた杭）の人を返す
#   doc_line(...) … 根拠の文書1件の表示用の行
#
# 順位づけの設定は、評価セット11問で確かめた既定値（全文の比重0.5、経験の厚み0.3、役割は既定の重み）。
# 看板は順位づけに使わない（rank_people の alpha=1）。看板は、人を選んだあとの「もっと調べる」でだけ使う。
# 開発用の確認画面（_test/rank/app.py）は、これとは別に、比重を動かして試すためのもの。
# =============================================================
from search import embed, profiles as P, rank, retrieve

DEFAULTS = {"w_fts": 0.5, "beta": 0.3, "top_n": 5, "vec_min": 0.30, "fts_min": 0.0}


def _fetch_all(sb, table, cols, order, page=1000):
    """表を全部読む。並べ替えのキーは、行が一意に決まる列の組にすること"""
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


def load(sb):
    """順位づけと表示に必要なデータをDBから読む"""
    sections, matrix = retrieve.load_section_vectors(sb)
    docs = _fetch_all(sb, "documents", "doc_id,doc_code,title,doc_type,result,pj_status,submitted_at,started_at", ["doc_id"])
    authors = _fetch_all(sb, "document_authors", "doc_id,emp_id,role", ["doc_id", "emp_id"])
    emps = _fetch_all(sb, "employees", "emp_id,emp_no,name,dept_id,is_active", ["emp_id"])
    depts = _fetch_all(sb, "departments", "dept_id,dept_name", ["dept_id"])
    person_docs = {}                            # 社員ID → [(文書ID, 役割)]。「紐づくナレッジ」の表示に使う
    for a in authors:
        person_docs.setdefault(a["emp_id"], []).append((a["doc_id"], a["role"]))
    career = {}                                 # 社員ID → キャリアシートの項目名のリスト
    try:
        for r in _fetch_all(sb, "profile_notes", "note_id,emp_id,memo_kind", ["note_id"]):
            if r.get("memo_kind"):
                career.setdefault(r["emp_id"], []).append(r["memo_kind"])
    except Exception:                           # memo_kind の列がまだないDBでも、画面は動かす
        career = {}
    return {
        "person_docs": person_docs, "career": career,
        "sections": sections, "matrix": matrix,
        "corpus": rank.Corpus.from_rows(sections[["section_id", "doc_id"]].to_dict("records"), docs, authors, emps),
        "docs": {d["doc_id"]: d for d in docs},
        "emps": {e["emp_id"]: e for e in emps},
        "dept_name": {d["dept_id"]: d["dept_name"] for d in depts},
        "dept_id_of": {d["dept_name"]: d["dept_id"] for d in depts},
    }


def search(sb, ctx, query, dept_names=(), year_range=None, top_n=DEFAULTS["top_n"], role_levels=None, w_fts=None,
           vec_min=None, fts_min=None):
    """質問文から、人を探す。
    dept_names: 絞り込みの部署名（人の今の所属で絞る）。空ならすべて
    year_range: (最初の年, 最後の年)。文書の日付の年で絞る。None なら絞らない
    role_levels: 役割ごとの重みの段階 {役割: "重視"|"ふつう"|"あまり考慮しない"}。None なら既定（rank.DEFAULT_ROLE_LEVELS）
    w_fts: 全文検索の比重（0〜1）。0＝ベクトルのみ／1＝全文のみ。None なら既定
    vec_min: ベクトルの足切り。質問と章のコサイン類似度がこれ未満なら、その章のベクトルの点は付けない
    fts_min: 全文の足切り。全文スコア（PGroonga）がこれ未満なら、その章の全文の点は付けない
      どちらの点も付かない章は、点数に入らない。誰も残らなければ res["none"]=True（「該当なし」と出す）
    戻り値: {"proven": [人], "hidden": [人], "warning": ..., "weights": {"vector", "fts"}, "fts_used": bool, "beta": β}
    人 = {"emp", "dept_id", "score", "frame", "evidence": [...], "parts": {"vector", "fts"}}
      evidence の各要素に、章の順位と点数の内訳（vec_rank, fts_rank, vec_part, fts_part, role_weight, counted）を足してある

    点数の内訳（画面で「何がどう響いているか」を見せるため）：
      章の点数 ＝ ベクトルの比重 × 1/(60+ベクトル順位) ＋ 全文の比重 × 1/(60+全文順位)      …RRF。2つの項が、そのままベクトル分・全文分
      人の点数 ＝ 最上位の文書の貢献 ＋ β × 2番目の文書の貢献      文書の貢献 ＝ 章の点数 × 役割の重み（× 新しさ）
      この式は章の点数について線形なので、人の点数も「ベクトル分 ＋ 全文分」にきれいに分けられる（合計は元の点数と一致する）"""
    w = DEFAULTS["w_fts"] if w_fts is None else w_fts
    vec_min = DEFAULTS["vec_min"] if vec_min is None else vec_min
    fts_min = DEFAULTS["fts_min"] if fts_min is None else fts_min
    qvec = embed.embed_query(query)
    vdf = retrieve.vector_search(qvec, ctx["sections"], ctx["matrix"], k=retrieve.FTS_N)
    vec = vdf["section_id"].tolist()
    vec_cos = dict(zip(vdf["section_id"], vdf["score"]))        # 質問と章のコサイン類似度（足切りに使う）
    warning = None
    try:
        pairs = retrieve.fulltext_search_scored(sb, query, n=retrieve.FTS_N)
    except Exception as e:                      # 全文検索の関数がない、など。ベクトルだけで探す
        pairs, warning = None, str(e)[:300]
    fts = [sid for sid, _ in pairs] if pairs else None
    fts_score = dict(pairs) if pairs else {}
    fts_used = bool(fts)                        # 全文で当たる章がなければ、ベクトルだけになる
    wv, wf = (1 - w, w) if fts_used else (1.0, 0.0)
    rv = {sid: r for sid, r in rank.rrf([vec]).items() if vec_cos[sid] >= vec_min}            # 足切りを通ったものだけ点が付く
    rf = {sid: r for sid, r in rank.rrf([fts]).items() if fts_score[sid] >= fts_min} if fts_used else {}
    scores = {sid: wv * rv.get(sid, 0.0) + wf * rf.get(sid, 0.0) for sid in set(rv) | set(rf)}
    depts = {ctx["dept_id_of"][n] for n in dept_names if n in ctx["dept_id_of"]} or None
    years = set(range(year_range[0], year_range[1] + 1)) if year_range else None
    beta = DEFAULTS["beta"]
    res = rank.rank_people(scores, ctx["corpus"], depts=depts, years=years, top_n=top_n,
                           role_weight=rank.role_weights({**rank.DEFAULT_ROLE_LEVELS, **(role_levels or {})}), beta=beta)
    vec_rank = {sid: i for i, sid in enumerate(vec, 1)}
    fts_rank = {sid: i for i, sid in enumerate(fts or [], 1)} if fts_used else {}
    for frame in ("proven", "hidden"):
        for p in res[frame]:
            pv = pf = 0.0
            for i, e in enumerate(p["evidence"]):
                sid = e["section"]
                vp, fp = wv * rv.get(sid, 0.0), wf * rf.get(sid, 0.0)
                total = vp + fp
                e.update({"vec_rank": vec_rank.get(sid), "fts_rank": fts_rank.get(sid), "vec_part": vp, "fts_part": fp,
                          "vec_cos": float(vec_cos[sid]) if sid in vec_cos else None, "fts_score": fts_score.get(sid),
                          "vec_ok": sid in rv, "fts_ok": sid in rf,
                          "section_score": total, "role_weight": (e["score"] / total) if total else 0.0, "counted": i < 2})
                if i < 2 and total:                 # 人の点数に入るのは、上位2つの文書だけ（2番目は β 倍）
                    k = 1.0 if i == 0 else beta
                    pv += e["score"] * vp / total * k
                    pf += e["score"] * fp / total * k
            p["parts"] = {"vector": pv, "fts": pf}
            top = p["evidence"][0]                  # 信頼度：一番点の高い文書の章で決める
            p["confidence"] = "高" if (top["vec_ok"] and top["fts_ok"]) else ("意味" if top["vec_ok"] else "言葉")
    res.update({"none": not (res["proven"] or res["hidden"]), "vec_min": vec_min, "fts_min": fts_min, "warning": warning, "weights": {"vector": wv, "fts": wf}, "fts_used": fts_used, "beta": beta})
    return res


def doc_line(ctx, evidence):
    """根拠の文書1件の表示用。戻り値：(文書番号, 表題, 種類と状態, この人の役割)"""
    d = ctx["docs"][evidence["doc"]]
    return d["doc_code"], d["title"], P.doc_label(d), evidence["role"]


def doc_icon(ctx, doc_id):
    """文書の種類の絵文字（一覧の行頭に付ける）"""
    return P.doc_icon(ctx["docs"][doc_id])


def person_line(ctx, emp):
    """人の表示用。戻り値：(名前, 社員番号, 部署名)"""
    e = ctx["emps"][emp]
    return e["name"], e["emp_no"], ctx["dept_name"].get(e["dept_id"], "")



def person_knowledge(ctx, emp, hit_docs=()):
    """この人に紐づくナレッジ（文書とキャリアシート）。新しい順。
    hit_docs：今回の質問に当たった文書ID（印を付けるため）。
    戻り値：{"docs": [{"code", "title", "label", "role", "date", "hit"}], "career": [項目名]}"""
    items = []
    person_docs = ctx.get("person_docs")
    if person_docs is None:                     # 古い版のデータがキャッシュに残っているとき：その場で作る
        person_docs = {}
        for doc_id, people in ctx["corpus"].authors.items():
            for e, role in people:
                person_docs.setdefault(e, []).append((doc_id, role))
    for doc_id, role in person_docs.get(emp, []):
        d = ctx["docs"].get(doc_id)
        if d:
            items.append({"doc_id": doc_id, "code": d["doc_code"], "title": d["title"], "label": P.doc_label(d), "icon": P.doc_icon(d), "role": role,
                          "date": P.doc_date(d), "hit": doc_id in set(hit_docs)})
    items.sort(key=lambda x: x["date"], reverse=True)
    return {"docs": items, "career": ctx.get("career", {}).get(emp, [])}


TYPE_NAMES = {"proposal": "改善提案書", "project": "PJ文書", "idea": "アイデア投稿"}


def role_catalog(ctx):
    """役割ごとに、どの種類の文書の役割か、何件あるか。画面の「役割の重み」の説明に使う。
    戻り値：{役割: {"type": 文書の種類の名前, "count": 件数}}（件数は、その役割で書かれた文書の数）"""
    from collections import Counter
    c = Counter()
    for doc_id, people in ctx["corpus"].authors.items():
        t = ctx["corpus"].docs.get(doc_id, {}).get("doc_type")
        for _emp, role in people:
            c[(role, t)] += 1
    out = {}
    for (role, t), n in c.items():
        if role not in out or n > out[role]["count"]:
            out[role] = {"type": TYPE_NAMES.get(t, str(t)), "count": n}
    return out


def document_detail(sb, ctx, doc_id):
    """文書1件の中身（ポップアップ・詳細表示用）。章は章番号の順。
    戻り値：{"code", "title", "label", "date", "authors": [(名前, 社員番号, 役割)], "sections": [{"section_id", "section_no", "name", "role", "body"}]}"""
    d = ctx["docs"][doc_id]
    rows = (sb.table("document_sections").select("section_id,section_no,section_name,section_role,body")
            .eq("doc_id", doc_id).order("section_no").execute().data)
    authors = [(ctx["emps"][e]["name"], ctx["emps"][e]["emp_no"], role)
               for e, role in ctx["corpus"].authors.get(doc_id, []) if e in ctx["emps"]]
    return {"code": d["doc_code"], "title": d["title"], "label": P.doc_label(d), "badge": P.doc_badge(d),
            "kind_note": P.doc_kind_note(d), "date": P.doc_date(d), "authors": authors,
            "sections": [{"section_id": r["section_id"], "section_no": r["section_no"], "name": r["section_name"],
                          "role": r["section_role"], "body": r["body"]} for r in rows]}
