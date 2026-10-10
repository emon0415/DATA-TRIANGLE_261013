"""
隠れ出る杭検索：「人を探す」は本物の検索（search/service.py）、「文書を登録する」は register/page.py につながる
起動：streamlit run app.py
前提：.env に SUPABASE_URL、SUPABASE_KEY、OPENAI_API_KEY がある。DBに文書・章・埋め込みが入っている
"""
import streamlit as st

st.set_page_config(page_title="隠れ出る杭検索", page_icon="🔎", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans JP', sans-serif; }
.person { border-left: 6px solid var(--bar); padding: .2rem 0 .2rem 1rem; margin: .4rem 0 .2rem; }
.person.track { --bar: #3E5C76; }
.person.hidden { --bar: #C9962B; }
.person h4 { margin: 0; font-weight: 700; }
.person .dept { color: #55636E; font-size: .9rem; }
.kw { display: inline-block; background: #E9EDF0; border-radius: 4px; padding: 1px 8px; margin: 4px 4px 0 0; font-size: .85rem; }
.lead { color: #55636E; margin-top: -.6rem; }
.score { font-size: .92rem; margin: .1rem 0 .15rem 1.1rem; }
.score .v, .legend .v { color: #3E5C76; font-weight: 700; }
.score .f, .legend .f { color: #C9962B; font-weight: 700; }
.bar { display: flex; height: 8px; margin: 0 0 .5rem 1.1rem; max-width: 22rem; border-radius: 4px; overflow: hidden; background: #E9EDF0; }
.bar .v { background: #3E5C76; }
.bar .f { background: #C9962B; }
.legend { color: #55636E; font-size: .88rem; }
.person { scroll-margin-top: 4.5rem; }   /* クリックで飛んだとき、上のヘッダーに隠れないようにする */
.person.flash { animation: flashbg 1.8s ease-out; }
@keyframes flashbg { 0% { background: #FFF1C9; } 100% { background: transparent; } }
.ends { display: flex; justify-content: space-between; color: #55636E; font-size: .8rem; margin: -.6rem 0 .6rem; }
</style>
""", unsafe_allow_html=True)

# ---------- 画面の切り替え ----------
page = st.sidebar.radio("画面", ["人を探す", "仲間を探す", "属人化マップ", "空白地帯マップ", "文書を登録する", "個人看板を検索（開発用）"], label_visibility="collapsed")

# ========== 人を探す ==========
@st.cache_resource(show_spinner="検索の準備をしています（初回だけ時間がかかります）")
def get_search():
    from register import db
    from search import service
    sb = db.connect()
    return sb, service.load(sb)


@st.cache_resource(show_spinner="看板のベクトルを読んでいます")
def get_vectors():
    from search import similar
    return similar.load_vectors(get_search()[0])


@st.cache_data(show_spinner=False)
def run_search(query, depts, year_range, role_levels, w_fts, vec_min, fts_min):
    from search import service
    sb, ctx = get_search()
    return service.search(sb, ctx, query, dept_names=depts, year_range=year_range, role_levels=dict(role_levels), w_fts=w_fts,
                          vec_min=vec_min, fts_min=fts_min)


# 横幅いっぱいに広げる指定。新しい版の Streamlit は width="stretch"、古い版は use_container_width=True
import inspect
STRETCH = {"width": "stretch"} if "width" in inspect.signature(st.altair_chart).parameters else {"use_container_width": True}


def ends(left, right, where=st):
    """バーの下に、左端と右端の意味を出す（どちらに動かすと何が起きるかを分かりやすくする）"""
    where.markdown(f'<div class="ends"><span>← {left}</span><span>{right} →</span></div>', unsafe_allow_html=True)


def reset_roles():
    from search import rank, service
    st.session_state["w_fts"] = service.DEFAULTS["w_fts"]
    st.session_state["vec_min"] = service.DEFAULTS["vec_min"]
    st.session_state["fts_min"] = service.DEFAULTS["fts_min"]
    for role, level in rank.DEFAULT_ROLE_LEVELS.items():
        st.session_state[f"role_{role}"] = level


def render_document(detail, hit=None):
    """文書の中身を、章ごとのマークダウンで見せる。hit：今回の質問に当たった章の情報（evidence の1件）。当たった章に印を付ける"""
    st.markdown(f"**{detail['badge']}**　{detail['code']}／{detail['date']}")
    st.caption(detail["kind_note"])
    st.caption("書いた人：" + "、".join(f"{n}（{no}／{role}）" for n, no, role in detail["authors"]))
    for sec in detail["sections"]:
        is_hit = bool(hit) and sec["section_id"] == hit["section"]
        st.markdown(f"#### {'🎯 ' if is_hit else ''}{sec['name']}　<small>（{sec['role']}）</small>", unsafe_allow_html=True)
        if is_hit:
            rk = lambda r: f"{r}位" if r else "圏外"
            st.caption(f"この章が、今回の相談に当たりました（ベクトル {rk(hit['vec_rank'])}／全文 {rk(hit['fts_rank'])}）")
        st.markdown(sec["body"].replace("\n", "  \n"))


def open_document(doc_id, hit=None):
    """文書の中身を、ポップアップで開く（st.dialog がない版の Streamlit では、その場に出す）"""
    from search import service
    sb, ctx = get_search()
    detail = service.document_detail(sb, ctx, doc_id)
    if hasattr(st, "dialog"):
        @st.dialog(detail["title"], width="large")
        def _show():
            render_document(detail, hit)
        _show()
    else:
        with st.expander(detail["title"], expanded=True):
            render_document(detail, hit)


@st.cache_resource(show_spinner="つながりマップの準備をしています（初回だけ時間がかかります）")
def get_network():
    from search import network
    sb, ctx = get_search()
    return network.load(sb, ctx, profile=get_vectors())


@st.cache_resource(show_spinner="テーマ分けをしています（初回だけ時間がかかります）")
def get_themes():
    from search import themes
    sb, ctx = get_search()
    return themes.load(sb, ctx)


def network_controls(inline=False):
    """サイドバーに、つながりマップの設定を出す。戻り値：(t, 知っている範囲の段階, 人数)。
    inline=True（仲間を探す）は、サイドバーに直接置く。それ以外の画面は、エクスパンダーに入れる（設定は初期のまま使うことが多いので）"""
    from search import network as N
    box = st.sidebar if inline else st.sidebar.expander("つながりマップの設定")
    with box:
        if inline:
            st.markdown("### つながりの探し方")
        t = st.slider("つながりの重視", 0.0, 1.0, N.DEFAULTS["t"], 0.05, key="net_t",
                      help="左に動かすと、共同作業が多い人や同じ部署の人など、すでに関係の強い人が上に来ます。"
                           "右に動かすと、関心は近いのに接点のない人（新しい出会い）が上に来ます。")
        ends("すでに強い関係", "新しい出会い", st)
        rng = st.radio("知っている範囲", list(N.RANGE_OPTIONS), index=list(N.RANGE_OPTIONS).index(N.DEFAULTS["range"]),
                       key="net_range", help="ここまでを「すでに知っている人」とみなして、新しい出会いを優先するときに下げます。"
                                            "共同作業をした人は、いつも知っている人として扱います。")
        n = st.slider("表示する人数", 5, 15, N.DEFAULTS["top_n"], 1, key="net_n")
    return t, N.RANGE_OPTIONS[rng], n, True


def fact_params(kind):
    """設定を出さない画面（人を探す・属人化マップ）のつながりマップの固定値。看板は使わない"""
    from search import network as N
    f = N.FACT[kind]
    return f["t"], f["range"], f["top_n"], False


def level_legend():
    from search import network as N
    st.markdown("".join(f'<span class="kw" style="border-left:10px solid {c}">{l}</span>' for l, c in zip(N.LEVEL_LABELS, N.LEVEL_COLORS)),
                unsafe_allow_html=True)


NET_DOCS_SHOWN = 5       # つながりマップのカードに、最初に並べる文書の数


def show_network(emp, params, where="page"):
    """つながりマップ：選んだ人を中心に、つながりの強い人を物理シミュレーションの図で見せる。図の下に、その人たちのカードを出す。
    丸をクリックすると、下のその人のカードまで動く（カードの名前の左の色は、丸の色と同じ）。
    params：network_controls() の戻り値。ポップアップ（st.dialog）の中ではサイドバーの部品を作れないので、設定は画面の側で作って渡す。
    where："page"＝画面の中／"dialog"＝ポップアップの中（ポップアップの中では、文書を開くポップアップを重ねられないので、文書の一覧は出さない）"""
    import streamlit.components.v1 as components
    from search import network as N, service
    sb, ctx = get_search()
    t, known_range, top_n, use_profile = params
    net = get_network()
    res = N.neighbors(net, ctx, emp, t=t, known_range=known_range, top_n=top_n, use_profile=use_profile)
    if res["status"] == "no_docs":
        st.info("この人は、関わった文書も看板もないため、つながりを描けません。")
        return
    html = N.build_html(ctx, net, emp, res["people"])      # 画面に出す文章は、DBの文書の中身ではなく、名前・部署名・語だけ
    if hasattr(st, "iframe"):                              # 新しい版の Streamlit。components.html は、将来なくなる予定
        st.iframe(html, height=580)
    else:
        components.html(html, height=580)
    level_legend()
    if use_profile:
        st.caption("丸の色は、中心の人との組織の近さ。◆は、看板（関心）は近いのに、文書では接点が薄い人＝意外なつながり。"
                   "太く短い線ほど、つながりが強い。線は、実線＝共同作業、紫の点線＝看板（関心）が近い、灰色の点線＝文書のテーマ・文章が近い。"
                   "丸にマウスを乗せると共通する語が出ます（看板の中身は出しません）。ドラッグで動かせます。丸をクリックすると、下のその人のカードに移ります。")
    else:
        st.caption("丸の色は、中心の人との組織の近さ。太く短い線ほど、つながりが強い。実線＝共同作業、灰色の点線＝文書のテーマ・文章が近い。"
                   "この画面のつながりは、文書と組織という事実だけで描いています（看板は使っていません）。"
                   "丸にマウスを乗せると共通する語が出ます。ドラッグで動かせます。丸をクリックすると、下のその人のカードに移ります。")
    st.markdown("##### つながりマップの人")
    for r in res["people"]:
        e = r["emp"]
        name, no, dept = service.person_line(ctx, e)
        a, b, c, d = r["parts"]
        st.markdown(f'<div class="person" id="mp-{e}" style="border-left:6px solid {N.LEVEL_COLORS[r["level"]]}"><h4>{name}</h4>'
                    f'<div class="dept">{dept}／社員番号 {no}</div></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="score">つながりの強さ <b>{r["score"]:.2f}</b> ＝ {"看板 %.2f ＋ " % a if use_profile else ""}テーマ {b:.2f} ＋ 文章 {c:.2f} ＋ 知っている度 {d + 0.0:+.2f}</div>',
                    unsafe_allow_html=True)
        tags = [N.LEVEL_LABELS[r["level"]]]
        if r["surprise"]:
            tags.append("◆ 意外なつながり（関心は近いが、文書では接点が薄い）")
        if r["words"]:
            tags.append("共通する語：" + "、".join(r["words"]))
        if r["shared_docs"]:
            tags.append(f"共同作業 {len(r['shared_docs'])}件")
        st.caption("　｜　".join(tags))
        k = service.person_knowledge(ctx, e, hit_docs=r["shared_docs"])
        sm = N.person_summary(net, ctx, e, shared=r["shared_docs"])
        c = sm["counts"]
        st.markdown("**この人のナレッジ**　" + (f"文書 {sm['total']}件（完了PJ {c['完了PJ']}・採択提案 {c['採択提案']}・ほか {c['ほか']}）" if sm["total"] else "公開された文書はありません"))
        if sm["words"]:
            st.markdown("得意なテーマ：" + " ".join(f'<span class="kw">{w}</span>' for w in sm["words"]), unsafe_allow_html=True)
        shown = sm["docs"][:NET_DOCS_SHOWN]
        if len(sm["docs"]) > NET_DOCS_SHOWN and st.checkbox(f"残りの文書 {len(sm['docs']) - NET_DOCS_SHOWN}件も表示する", key=f"mpmore|{where}|{emp}|{e}"):
            shown = sm["docs"]
        for d in shown:                                   # この人が紐づいている文書を、そのまま並べる。中身も見られる
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{'★ ' if d['shared'] else ''}{d['icon']} {d['title']}　{d['code']}／{d['label']}／{d['role']}")
            if where == "page":                           # ポップアップの中では、文書を開くポップアップを重ねられないので、その場に広げる
                if c2.button("中身を見る", key=f"mpdoc|{emp}|{e}|{d['doc_id']}"):
                    open_document(d["doc_id"])
            elif c2.checkbox("中身", key=f"mpdocx|{emp}|{e}|{d['doc_id']}"):
                render_document(service.document_detail(sb, ctx, d["doc_id"]))
        if sm["docs"]:
            st.caption("★は、中心の人と共同作業した文書です。実績のある文書（完了PJ・採択提案）を先に並べています。")
        st.markdown("キャリアシート：" + ("、".join(k["career"]) if k["career"] else "なし"))
        show_brief(e, "", [d["doc_id"] for d in k["docs"]], where=f"net-{where}", with_message=False)
        st.divider()


def open_network(emp, params):
    """つながりマップを、ポップアップで開く（st.dialog がない版の Streamlit では、その場に出す）"""
    from search import service
    name, _, dept = service.person_line(get_search()[1], emp)
    if hasattr(st, "dialog"):
        @st.dialog(f"{name}さんのつながりマップ", width="large")
        def _show():
            show_network(emp, params, where="dialog")
        _show()
    else:
        st.markdown(f"#### {name}さんのつながりマップ")
        show_network(emp, params, where="dialog")


def show_brief(emp, query, doc_ids, where="main", with_message=True):
    """「この人についてもっと調べる」：その人のノートと、質問に当たった文書の章から、AIが紹介を書く（押したときだけ）。
    紹介が出たあとに、「相談メッセージの下書きを作る」ボタンが出る（紹介の出典つきの経験だけを材料に、依頼文を書く）"""
    from search import embed, outreach, person_brief as pb, service
    sb, ctx = get_search()
    key = f"brief|{emp}|{query}"
    if st.button("この人についてもっと調べる", key=f"btn|{where}|{key}"):
        with st.spinner("この人のノートを読んで、紹介を書いています"):
            full = pb.fetch_sections(sb, doc_ids[:pb.FULL_TEXT_DOCS])
            st.session_state[key] = pb.generate(embed._client(), pb.gather(sb, emp), query=query, full_texts=full)
    brief = st.session_state.get(key)
    if brief:
        st.markdown(pb.to_markdown(brief))
        if brief["dropped"]:
            st.caption("※文書で確かめられなかった記述は、除いています。")
        if not with_message:                            # つながりマップの中では、相談メッセージの下書きは出さない
            return
        mkey = f"msg|{emp}|{query}"
        topic = query
        if not query:                                   # 「仲間を探す」から来たときは、相談したいことが決まっていない
            topic = st.text_input("相談したいこと（なくてもよい）", key=f"mq|{where}|{emp}")
        if st.button("相談メッセージの下書きを作る", key=f"msgbtn|{where}|{mkey}"):
            with st.spinner("下書きを書いています"):
                name, _, dept = service.person_line(ctx, emp)
                st.session_state[mkey] = outreach.generate(embed._client(), name, dept, topic, brief)
        out = st.session_state.get(mkey)
        if out and out["empty"]:
            st.info("この人の紹介に、出典のある経験が書かれていないため、下書きは書けません。")
        elif out:
            st.text_area("下書き（差出人の名前と所属を直して、使ってください）", out["text"], height=280,
                         key=f"msgtxt|{where}|{mkey}|{hash(out['text'])}")
            st.caption("紹介に書かれた経験だけを材料にしています。送る前に、内容を読んで直してください。")
            if out["dropped"]:
                st.caption("※材料にない文書番号を含む文は、除いています。")


if page == "人を探す":
    try:
        sb, ctx = get_search()
    except Exception as e:
        st.error(f"検索の準備ができませんでした：{e}")
        st.stop()
    with st.sidebar:
        st.subheader("条件")
        depts = st.multiselect("部署", sorted(ctx["dept_name"].values()), placeholder="すべての部署")
        years = st.slider("年", 2016, 2026, (2016, 2026))
        from search import rank, service
        st.session_state.setdefault("w_fts", service.DEFAULTS["w_fts"])
        w_fts = st.slider("ベクトル ⇄ 全文の比率", 0.0, 1.0, step=0.05, key="w_fts",
                          help="右に動かすと、言葉が一致する章を重く見ます。左に動かすと、意味が近い章を重く見ます。"
                               "真ん中は、両方を同じ重さで見ます。")
        ends("ベクトル重視", "全文重視")
        st.caption(f"ベクトル {round((1 - w_fts) * 100)}％　／　全文 {round(w_fts * 100)}％")
        st.session_state.setdefault("vec_min", service.DEFAULTS["vec_min"])
        st.session_state.setdefault("fts_min", service.DEFAULTS["fts_min"])
        vec_min = st.slider("ベクトルの足切り（質問との類似度）", 0.0, 1.0, step=0.01, key="vec_min",
                            help="右に動かすと、質問にかなり近い章だけが残ります。左に動かすと、弱い当たりも残ります。"
                                 "（質問と章のコサイン類似度が、この値より低い章には、ベクトルの点を付けません）")
        ends("ゆるい", "厳しい")
        fts_min = st.slider("全文の足切り（全文スコア）", 0.0, 20.0, step=0.5, key="fts_min",
                            help="右に動かすと、質問の言葉が多く当たった章だけが残ります。左に動かすと、1語でも当たった章が残ります。"
                                 "「内訳」に出る全文スコアを見て、決めてください")
        ends("ゆるい", "厳しい")
        with st.expander("役割の重み（探し方の調整）", expanded=True):
            from search import service
            cat = service.role_catalog(ctx)
            st.caption("文書を書いた人の「役割」ごとに、その文書の点数を、人の順位にどれだけ反映するか。"
                       "重視＝1.0／ふつう＝0.6／あまり考慮しない＝0.3。役割は、文書の種類ごとに決まっています。")
            for role, default in rank.DEFAULT_ROLE_LEVELS.items():
                st.session_state.setdefault(f"role_{role}", default)
            levels = {}
            groups_ui = [("改善提案書", "提案者の経験（採択・不採択・保留・審査中のすべて）"),
                         ("アイデア投稿", "投稿者の経験（現場の困りごとと、アイデア）"),
                         ("PJ文書", "PJでの関わり方（主担当・副担当・責任者）")]
            for type_name, note in groups_ui:
                roles = [r for r in rank.DEFAULT_ROLE_LEVELS if cat.get(r, {}).get("type") == type_name]
                if not roles:
                    continue
                st.markdown(f"**{type_name}**")
                st.caption(note)
                for role in roles:
                    levels[role] = st.select_slider(role, options=list(rank.ROLE_LEVELS),
                                                    key=f"role_{role}")
            for role in rank.DEFAULT_ROLE_LEVELS:        # どの種類にも入らない役割があっても、既定の重みで探す
                levels.setdefault(role, st.session_state[f"role_{role}"])
            st.button("既定に戻す", on_click=reset_roles)
    net_params = fact_params("人を探す")

    st.title("隠れ出る杭検索")
    st.markdown('<p class="lead">困っていることを書くと、似た経験を持つ人を社内の文書から探します。</p>',
                unsafe_allow_html=True)
    DEFAULT_QUERY = ("納入から15年以上たったエレベーターで、モータや巻上機が突然止まる故障が、今年だけで5件ありました。"
                     "うち2件は、利用者が乗っている最中の停止で、お客様からの信頼を大きく損ねました。"
                     "定期点検で保守員が気づける異常には限りがあり、「あと数日早く気づけていれば」という後悔が続いています。"
                     "運転中の振動、電流、温度などのデータを使って、故障する前に異常に気づける仕組みを作りたいのですが、"
                     "センサの選び方、しきい値の決め方、誤検知を減らす方法が分かりません。似た取り組みをされた方に、話を聞きたいです。")
    q = st.text_area("相談したいこと", DEFAULT_QUERY, height=190)
    if st.button("人を探す", type="primary") and q.strip():
        st.session_state.query = q.strip()

    query = st.session_state.get("query")
    if query:
        from search import service
        res = run_search(query, tuple(depts), None if years == (2016, 2026) else years, tuple(levels.items()), w_fts, vec_min, fts_min)
        if res["warning"]:
            st.warning("全文検索を呼べなかったため、意味の近さだけで探しています。")
        st.caption(f"相談したいこと：{query}")
        if res["none"]:
            st.info("社内に、この相談に合うナレッジは見つかりませんでした。"
                    f"（足切り：ベクトル {vec_min:.2f}／全文 {fts_min:g}。下げると、弱い当たりも出ます）")
            st.stop()
        if not res["fts_used"] and not res["warning"]:
            st.caption("※全文検索で当たる章がなかったため、ベクトルだけで点数を付けています。")
        st.markdown('<div class="legend">点数の見方：<span class="v">■ベクトル</span>（意味の近さ）と '
                    '<span class="f">■全文</span>（言葉の一致）の点を足したものが、人の点数です。'
                    '大きいほど、相談に近い人です。</div>', unsafe_allow_html=True)

        def card(p, kind):
            name, no, dept = service.person_line(ctx, p["emp"])
            st.markdown(f'<div class="person {kind}"><h4>{name}</h4>'
                        f'<div class="dept">{dept}／社員番号 {no}</div></div>', unsafe_allow_html=True)
            st.caption({"高": "信頼度：高（意味も言葉も合う）", "意味": "信頼度：中（意味が近い。言葉は一致しない）",
                        "言葉": "信頼度：中（言葉が一致。意味の近さは足切り未満）"}[p["confidence"]])
            pv, pf = p["parts"]["vector"], p["parts"]["fts"]
            share = pv / (pv + pf) * 100 if (pv + pf) else 0
            st.markdown(f'<div class="score">合計 <b>{p["score"]:.4f}</b> ＝ <span class="v">ベクトル {pv:.4f}</span>'
                        f' ＋ <span class="f">全文 {pf:.4f}</span></div>'
                        f'<div class="bar"><span class="v" style="width:{share:.0f}%"></span>'
                        f'<span class="f" style="width:{100 - share:.0f}%"></span></div>', unsafe_allow_html=True)
            for e in p["evidence"]:
                code, title, label, role = service.doc_line(ctx, e)
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"{service.doc_icon(ctx, e['doc'])} {title}　{code}／{label}／{role}")
                if c2.button("中身を見る", key=f"ev|{p['emp']}|{e['doc']}"):
                    open_document(e["doc"], hit=e)
            with st.expander("点数の内訳"):
                st.caption(f"人の点数 ＝ 最上位の文書の点 ＋ {res['beta']}（経験の厚み）× 2番目の文書の点。"
                           "文書の点 ＝ 章の点 × 役割の重み。章の点 ＝ ベクトルの点 ＋ 全文の点（それぞれ、比率 × 1/(60＋順位)）")
                rk = lambda r: f"{r}位" if r else "圏外（上位100に入らず）"
                gate = lambda ok, r: "" if (ok or not r) else "（足切りで点なし）"
                for e in p["evidence"]:
                    code, title, _label, role = service.doc_line(ctx, e)
                    st.markdown(f"**{code}**　{title}（{role}）　{'人の点数に入る' if e['counted'] else '参考（点数には入らない）'}")
                    vc = f"類似度 {e['vec_cos']:.2f}／" if e["vec_cos"] is not None else ""
                    fs = f"全文スコア {e['fts_score']:g}／" if e["fts_score"] is not None else ""
                    st.markdown(f"- <span class='v'>ベクトル</span>：{vc}{rk(e['vec_rank'])} → {e['vec_part']:.4f}{gate(e['vec_ok'], e['vec_rank'])}"
                                f"　／　<span class='f'>全文</span>：{fs}{rk(e['fts_rank'])} → {e['fts_part']:.4f}{gate(e['fts_ok'], e['fts_rank'])}"
                                f"　／　章の点 {e['section_score']:.4f} × 役割の重み {e['role_weight']:.2f} ＝ 文書の点 {e['score']:.4f}",
                                unsafe_allow_html=True)
            k = service.person_knowledge(ctx, p["emp"], hit_docs=[e["doc"] for e in p["evidence"]])
            with st.expander(f"紐づくナレッジ（文書 {len(k['docs'])}件／キャリアシート {len(k['career'])}項目）"):
                hits = {e["doc"]: e for e in p["evidence"]}
                for d in k["docs"]:
                    c1, c2 = st.columns([5, 1])
                    c1.markdown(f"{'★ ' if d['hit'] else ''}{d['icon']} {d['title']}　{d['code']}／{d['label']}／{d['role']}／{d['date']}")
                    if c2.button("中身を見る", key=f"kn|{p['emp']}|{d['doc_id']}"):
                        open_document(d["doc_id"], hit=hits.get(d["doc_id"]))
                st.markdown("キャリアシート：" + ("、".join(k["career"]) if k["career"] else "なし"))
                st.caption("★は、今回の相談に当たった文書です。")
            show_brief(p["emp"], query, [e["doc"] for e in p["evidence"]])
            if st.button("つながりマップを見る", key=f"net|{p['emp']}"):
                open_network(p["emp"], net_params)

        groups = [("実績のある人", "完了したPJ、採択された提案で、実際に成果を出した人", "proven", "track"),
                  ("原石の人", "不採択・保留・審査中の提案や、アイデア投稿で、同じ課題に取り組んでいた人", "hidden", "hidden")]
        cols = st.columns(len(groups))
        for col, (label, desc, frame, kind) in zip(cols, groups):
            with col:
                st.subheader(label)
                st.caption(desc)
                for p in res[frame]:
                    card(p, kind)
                    st.divider()
                if not res[frame]:
                    st.write("該当する人がいません。部署や年の条件を外すと、表示されることがあります。")
    else:
        st.info("相談したいことを書いて「人を探す」を押してください。")

# ========== 仲間を探す ==========
elif page == "仲間を探す":
    from search import service

    try:
        sb, ctx = get_search()
    except Exception as e:
        st.error(f"準備ができませんでした：{e}")
        st.stop()
    net_params = network_controls(inline=True)

    st.title("仲間を探す")
    st.markdown('<p class="lead">社員番号を入れると、その人とつながりの強い人が、図と一覧で見つかります。'
                '看板（関心）の近さ、文書のテーマと文章の近さ、組織の近さをあわせて見ています。看板の中身は表示しません。</p>',
                unsafe_allow_html=True)
    no = st.text_input("社員番号").strip()
    emp = next((e for e, v in ctx["emps"].items() if v["emp_no"] == no), None) if no else None
    if no and emp is None:
        st.warning("その社員番号は見つかりません。")
    if emp:
        name, _, dept = service.person_line(ctx, emp)
        st.subheader(f"{name}（{dept}）")
        mk = service.person_knowledge(ctx, emp)
        with st.expander(f"この人のナレッジ（文書 {len(mk['docs'])}件／キャリアシート {len(mk['career'])}項目）"):
            for d in mk["docs"]:
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"{d['icon']} {d['title']}　{d['code']}／{d['label']}／{d['role']}／{d['date']}")
                if c2.button("中身を見る", key=f"mykn|{emp}|{d['doc_id']}"):
                    open_document(d["doc_id"])
            from search import person_brief as pb
            sheets = [n for n in pb.gather(sb, emp) if n["kind"] == pb.CAREER]
            if sheets:
                st.markdown("**キャリアシート**（本人が書いた項目）")
                for n in sheets:
                    st.markdown(f"**{n['label']}**")
                    st.caption(n["body"])
            elif not mk["docs"]:
                st.write("この人のナレッジは、まだありません。")
        show_network(emp, net_params)

# ========== 属人化マップ ==========
elif page == "属人化マップ":
    import altair as alt
    import pandas as pd
    from search import service, themes as TH

    try:
        sb, ctx = get_search()
        th = get_themes()
    except Exception as e:
        st.error(f"準備ができませんでした：{e}")
        st.stop()
    st.sidebar.markdown("### 属人化の見方")
    cut = st.sidebar.slider("「替えがききにくい」とみなす割合", 0.02, 0.30, TH.DEFAULTS["cut"], 0.01,
                            help="全文書のうち、他の人の文書との近さが低い順に、この割合までを「替えがききにくい文書」とします。"
                                 "右に動かすと、対象が増えます。")
    ends("厳しく（少なく）", "ゆるく（多く）", st.sidebar)
    only_away = st.sidebar.checkbox("退職・異動した人が関わる文書だけ見る",
                                    help="いま在職していない人、または文書を書いたときと別の部署にいる人が関わる文書に絞ります。")
    net_params = fact_params("属人化")

    st.title("属人化マップ")
    st.markdown('<p class="lead">社内の文書を、内容の近さで地図にしました。橙の点は、他の人の文書に似たものが見当たらない文書（替えがききにくい文書）です。'
                '書いた人が異動や退職でいなくなると、社内から見えなくなりやすい経験です。</p>', unsafe_allow_html=True)

    uniq = set(TH.unique_docs(th, cut))
    away_doc = lambda d: any((not ctx["emps"][e].get("is_active", True)) or (dep not in (None, ctx["emps"][e]["dept_id"]))
                             for e, dep in th["auth"].get(d, {}).items())
    rows = []
    for i, d in enumerate(th["doc_ids"]):
        doc = ctx["docs"][d]
        names = "、".join(ctx["emps"][e]["name"] for e in th["auth"].get(d, {}))
        rows.append({"x": float(th["xy"][i][0]), "y": float(th["xy"][i][1]), "表題": doc["title"], "文書番号": doc["doc_code"],
                     "種類": service.P.doc_label(doc), "書いた人": names, "テーマ": th["names"][int(th["lab"][i])],
                     "区分": "替えがききにくい" if i in uniq and (not only_away or away_doc(d)) else "ほかにも似た文書がある"})
    df = pd.DataFrame(rows)
    chart = (alt.Chart(df).mark_circle(size=46).encode(
        x=alt.X("x:Q", axis=None), y=alt.Y("y:Q", axis=None),
        color=alt.Color("区分:N", scale=alt.Scale(domain=["替えがききにくい", "ほかにも似た文書がある"], range=["#D9822B", "#C9D3DA"]),
                        legend=alt.Legend(orient="bottom", title=None)),
        opacity=alt.condition(alt.datum["区分"] == "替えがききにくい", alt.value(0.95), alt.value(0.55)),
        order=alt.Order("区分:N", sort="descending"),
        tooltip=["表題", "文書番号", "種類", "書いた人", "テーマ"]).properties(height=460).interactive())
    st.altair_chart(chart, **STRETCH)
    n_shown = int((df["区分"] == "替えがききにくい").sum())
    st.caption(f"全{len(df)}文書のうち、橙は{n_shown}件。近い文書ほど、地図で近くに並びます（内容の近さを2次元にしたもので、軸に意味はありません）。"
               "点にマウスを乗せると、文書の表題と書いた人が出ます。")

    table = [r for r in TH.person_table(th, ctx, cut) if not only_away or not r["active"] or r["moved"]]
    st.subheader("替えがききにくい経験を持つ人")
    st.caption("橙の文書（他の人の文書に似たものがない文書）を、多く書いている人の順です。この人が異動・退職すると、社内から見えなくなりやすい経験を持っています。")
    if not table:
        st.info("条件に合う人がいません。割合を上げると、表示されることがあります。")
        st.stop()
    show = pd.DataFrame([{"名前": ctx["emps"][r["emp"]]["name"], "部署": ctx["dept_name"].get(ctx["emps"][r["emp"]]["dept_id"], ""),
                          "替えがききにくい文書": r["unique"], "関わった文書": r["total"],
                          "状況": "、".join(x for x in (None if r["active"] else "退職", "異動あり" if r["moved"] else None) if x) or "在職"}
                         for r in table[:30]])
    st.dataframe(show, hide_index=True, **STRETCH)
    pick = st.selectbox("経験の引き継ぎ先を探したい人", range(min(len(table), 30)),
                        help="選んだ人の経験を、ほかに引き継げそうな人（近い内容の文書を書いた、在職の人）を、下に出します。",
                        format_func=lambda i: f"{show.iloc[i]['名前']}（{show.iloc[i]['部署']}）　{show.iloc[i]['替えがききにくい文書']}件")
    emp = table[pick]["emp"]
    st.markdown(f"#### {ctx['emps'][emp]['name']}さんの、替えがききにくい文書")
    for d, role in ctx["person_docs"].get(emp, []):
        if d in th["index"] and th["index"][d] in uniq:
            doc = ctx["docs"][d]
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{service.doc_icon(ctx, d)} {doc['title']}　{doc['doc_code']}／{service.P.doc_label(doc)}／{role}")
            if c2.button("中身を見る", key=f"uq|{emp}|{d}"):
                open_document(d)
    st.markdown(f"#### {ctx['emps'][emp]['name']}さんの経験を、引き継げそうな人")
    succ = TH.successors(th, ctx, emp, cut)
    if not succ:
        st.write("近い経験を持つ人が見つかりません。")
    for r in succ:
        name, no, dept = service.person_line(ctx, r["emp"])
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{name}**（{dept}）　近さ {r['sim']:.2f}　「{ctx['docs'][r['doc']]['title']}」")
        if c2.button("つながりマップ", key=f"succ|{emp}|{r['emp']}"):
            open_network(r["emp"], net_params)
    st.caption("この人の替えがききにくい文書に、内容が近い文書を書いた、在職の人です。同じ経験とは限らないので、引き継ぎの相談相手を探す目安にしてください。")

# ========== 空白地帯マップ ==========
elif page == "空白地帯マップ":
    import altair as alt
    import pandas as pd
    from search import service, themes as TH

    try:
        sb, ctx = get_search()
        th = get_themes()
        net = get_network()
    except Exception as e:
        st.error(f"準備ができませんでした：{e}")
        st.stop()
    st.sidebar.markdown("### 空白地帯の見方")
    min_docs = st.sidebar.slider("テーマの最低文書数", 3, 30, TH.DEFAULTS["min_docs"], 1,
                                 help="文書が少ないテーマは、解決の割合がぶれやすいので、この件数以上のテーマだけ「空白地帯」にします。")
    max_rate = st.sidebar.slider("解決の割合の上限", 0.0, 0.5, TH.DEFAULTS["max_rate"], 0.01,
                                 help="テーマの文書のうち、採択された提案・完了したPJの割合が、この値以下のテーマを「空白地帯」とします。")
    ends("厳しく（少なく）", "ゆるく（多く）", st.sidebar)

    st.title("空白地帯マップ")
    st.markdown('<p class="lead">社内の文書をテーマに分けて、「困りごと・提案はあるのに、解決まで届いていない」テーマを探します。'
                '解決＝採択された提案、完了したPJ。未解決＝アイデア投稿、不採択・保留の提案、中止したPJ。</p>', unsafe_allow_html=True)
    gaps = TH.gaps(th, ctx, min_docs, max_rate)
    df = pd.DataFrame([{"テーマ": g["name"], "文書数": g["n"], "解決": g["solved"], "取り組み中": g["open"], "未解決": g["unsolved"],
                        "解決の割合": round(g["rate"], 2), "区分": "空白地帯" if g["gap"] else "それ以外",
                        "ラベル": g["name"] if g["gap"] and i < 6 else ""} for i, g in enumerate(gaps)])    # 名前を出すのは、未解決の多い上位6テーマだけ（重なるため）
    base = alt.Chart(df).encode(
        x=alt.X("解決:Q", title="解決した文書の数"), y=alt.Y("未解決:Q", title="未解決の文書の数"),
        color=alt.Color("区分:N", scale=alt.Scale(domain=["空白地帯", "それ以外"], range=["#D9822B", "#9FB3C4"]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["テーマ", "文書数", "解決", "取り組み中", "未解決", "解決の割合"])
    st.altair_chart((base.mark_circle(opacity=.8).encode(size=alt.Size("文書数:Q", legend=None, scale=alt.Scale(range=[40, 600])))
                     + base.transform_filter(alt.datum["ラベル"] != "").mark_text(dx=9, dy=-9, align="left", fontSize=11).encode(text="ラベル:N")
                     ).properties(height=420).interactive(), **STRETCH)
    gap_list = [g for g in gaps if g["gap"]]
    st.caption(f"{len(gaps)}テーマのうち、空白地帯は{len(gap_list)}テーマ。左上（解決が少なく、未解決が多い）に出ます。"
               "名前を出しているのは、未解決の多い上位6テーマ。テーマ名は、そのテーマに特徴的なキーワードです。")
    if not gap_list:
        st.info("空白地帯のテーマがありません。解決の割合の上限を上げると、表示されることがあります。")
        st.stop()
    st.dataframe(df[df["区分"] == "空白地帯"].drop(columns=["区分", "ラベル"]), hide_index=True, **STRETCH)

    # どの職能の人が、その未解決を出しているか
    funcs = sorted({net["dept"][v["dept_id"]]["func"] for v in ctx["emps"].values()})
    cells = []
    for g in gap_list[:15]:
        c = {}
        for d in g["docs"]:
            if TH.status(ctx["docs"][d]) == TH.UNSOLVED:
                for e in th["auth"].get(d, {}):
                    f = TH.dept_func(ctx, net, e)
                    c[f] = c.get(f, 0) + 1
        cells += [{"テーマ": g["name"], "職能": f, "未解決の文書": n} for f, n in c.items()]
    if cells:
        with st.expander("空白地帯のテーマを、どの職能の人が出しているか", expanded=False):
            heat = alt.Chart(pd.DataFrame(cells)).mark_rect().encode(
                x=alt.X("職能:N", sort=funcs, title=None), y=alt.Y("テーマ:N", title=None),
                color=alt.Color("未解決の文書:Q", scale=alt.Scale(scheme="oranges")), tooltip=["テーマ", "職能", "未解決の文書"])
            st.altair_chart(heat.properties(height=26 * min(len(gap_list), 15) + 40), **STRETCH)
            st.caption("職能は、書いた人の今の所属から取っています。色が濃いほど、その職能の人が出した未解決の文書が多い。")

    pick = st.selectbox("中を見るテーマ", range(len(gap_list)),
                        format_func=lambda i: f"{gap_list[i]['name']}　（未解決 {gap_list[i]['unsolved']}／解決 {gap_list[i]['solved']}）")
    g = gap_list[pick]
    st.markdown(f"#### {g['name']}")
    for label, st_key, note in (("未解決の文書（同じ課題に取り組んでいた人＝原石の人）", TH.UNSOLVED, "書いた人は、このテーマの課題を見つけた人です。"),
                                ("解決した文書", TH.SOLVED, "ここに近い経験が、解決の手がかりになります。"),
                                ("取り組み中の文書", TH.OPEN, "")):
        ds = [d for d in g["docs"] if TH.status(ctx["docs"][d]) == st_key]
        if not ds:
            continue
        with st.expander(f"{label}：{len(ds)}件", expanded=(st_key == TH.UNSOLVED)):
            if note:
                st.caption(note)
            for d in ds[:15]:
                doc = ctx["docs"][d]
                who = "、".join(f"{ctx['emps'][e]['name']}（{ctx['dept_name'].get(ctx['emps'][e]['dept_id'], '')}）" for e in th["auth"].get(d, {}))
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"{service.doc_icon(ctx, d)} {doc['title']}　{doc['doc_code']}／{service.P.doc_label(doc)}　{who}")
                if c2.button("中身を見る", key=f"gp|{g['theme']}|{d}"):
                    open_document(d)
            if len(ds) > 15:
                st.caption(f"ほか {len(ds) - 15}件")

# ========== 個人看板（開発用） ==========
elif page == "個人看板を検索（開発用）":
    from search import person_brief as pb
    from search import service
    try:
        sb, ctx = get_search()
    except Exception as e:
        st.error(f"準備ができませんでした：{e}")
        st.stop()
    st.title("個人看板（開発用）")
    st.markdown('<p class="lead">社員番号を入れると、その人の看板（ノートを日付順に並べたもの）の中身が見られます。'
                '本番の画面には出さない、確認用の画面です。</p>', unsafe_allow_html=True)
    no = st.text_input("社員番号").strip()
    emp = next((e for e, v in ctx["emps"].items() if v["emp_no"] == no), None) if no else None
    if no and emp is None:
        st.warning("その社員番号は見つかりません。")
    if emp:
        name, _, dept = service.person_line(ctx, emp)
        st.subheader(f"{name}（{dept}）")
        row = sb.table("profiles").select("profile_text,generated_at,embedded_at").eq("emp_id", emp).execute().data
        if not row:
            st.info("この人の看板はまだありません（ノートがない人は、看板がありません）。")
        else:
            st.caption(f"看板の作成：{str(row[0]['generated_at'])[:16]}／埋め込み：{str(row[0]['embedded_at'])[:16]}")
            st.text_area("看板の文章（ノートを日付順に並べたもの）", row[0]["profile_text"], height=320, disabled=True)
            notes = pb.gather(sb, emp)
            st.markdown(f"**ノート {len(notes)}枚**（出典は文書番号。キャリアシートは、本人が書いた項目）")
            for n in notes:
                st.markdown(f"**{n['kind']}**｜`{n['source']}`｜{n['label']}｜{n['role']}｜{n['date']}　{n['title']}")
                st.caption(n["body"])

# ========== 文書を登録する ==========
else:
    from register.page import show  # 登録タブは本物（DB・OpenAIにつながる）。人を探す側はまだモック
    show()
