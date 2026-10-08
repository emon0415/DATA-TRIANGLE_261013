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
</style>
""", unsafe_allow_html=True)

# ---------- 画面の切り替え ----------
page = st.sidebar.radio("画面", ["人を探す", "文書を登録する", "個人看板を検索（開発用）"], label_visibility="collapsed")

# ========== 人を探す ==========
@st.cache_resource(show_spinner="検索の準備をしています（初回だけ時間がかかります）")
def get_search():
    from register import db
    from search import service
    sb = db.connect()
    return sb, service.load(sb)


@st.cache_data(show_spinner=False)
def run_search(query, depts, year_range, role_levels, w_fts):
    from search import service
    sb, ctx = get_search()
    return service.search(sb, ctx, query, dept_names=depts, year_range=year_range, role_levels=dict(role_levels), w_fts=w_fts)


def reset_roles():
    from search import rank, service
    st.session_state["w_fts"] = service.DEFAULTS["w_fts"]
    for role, level in rank.DEFAULT_ROLE_LEVELS.items():
        st.session_state[f"role_{role}"] = level


def render_document(detail, hit=None):
    """文書の中身を、章ごとのマークダウンで見せる。hit：今回の質問に当たった章の情報（evidence の1件）。当たった章に印を付ける"""
    st.markdown(f"**{detail['code']}**／{detail['label']}／{detail['date']}")
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


def show_brief(emp, query, doc_ids, where="main"):
    """「この人についてもっと調べる」：その人のノートと、質問に当たった文書の章から、AIが紹介を書く（押したときだけ）"""
    from search import embed, person_brief as pb
    sb, _ = get_search()
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
                          help="0＝ベクトル検索（意味の近さ）だけ／1＝全文検索（言葉の一致）だけ。"
                               "章の点数を、この比率で足し合わせます。")
        st.caption(f"ベクトル {round((1 - w_fts) * 100)}％　／　全文 {round(w_fts * 100)}％")
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

    st.title("隠れ出る杭検索")
    st.markdown('<p class="lead">困っていることを書くと、似た経験を持つ人を社内の文書から探します。</p>',
                unsafe_allow_html=True)
    q = st.text_area("相談したいこと", "設備が故障する前に異常に気づける仕組みを作りたい", height=90)
    if st.button("人を探す", type="primary") and q.strip():
        st.session_state.query = q.strip()

    query = st.session_state.get("query")
    if query:
        from search import service
        res = run_search(query, tuple(depts), None if years == (2016, 2026) else years, tuple(levels.items()), w_fts)
        if res["warning"]:
            st.warning("全文検索を呼べなかったため、意味の近さだけで探しています。")
        st.caption(f"相談したいこと：{query}")
        if not res["fts_used"] and not res["warning"]:
            st.caption("※全文検索で当たる章がなかったため、ベクトルだけで点数を付けています。")
        st.markdown('<div class="legend">点数の見方：<span class="v">■ベクトル</span>（意味の近さ）と '
                    '<span class="f">■全文</span>（言葉の一致）の点を足したものが、人の点数です。'
                    '大きいほど、相談に近い人です。</div>', unsafe_allow_html=True)

        def card(p, kind):
            name, no, dept = service.person_line(ctx, p["emp"])
            st.markdown(f'<div class="person {kind}"><h4>{name}</h4>'
                        f'<div class="dept">{dept}／社員番号 {no}</div></div>', unsafe_allow_html=True)
            pv, pf = p["parts"]["vector"], p["parts"]["fts"]
            share = pv / (pv + pf) * 100 if (pv + pf) else 0
            st.markdown(f'<div class="score">合計 <b>{p["score"]:.4f}</b> ＝ <span class="v">ベクトル {pv:.4f}</span>'
                        f' ＋ <span class="f">全文 {pf:.4f}</span></div>'
                        f'<div class="bar"><span class="v" style="width:{share:.0f}%"></span>'
                        f'<span class="f" style="width:{100 - share:.0f}%"></span></div>', unsafe_allow_html=True)
            for e in p["evidence"]:
                code, title, label, role = service.doc_line(ctx, e)
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"📄 {title}　{code}／{label}／{role}")
                if c2.button("中身を見る", key=f"ev|{p['emp']}|{e['doc']}"):
                    open_document(e["doc"], hit=e)
            with st.expander("点数の内訳"):
                st.caption(f"人の点数 ＝ 最上位の文書の点 ＋ {res['beta']}（経験の厚み）× 2番目の文書の点。"
                           "文書の点 ＝ 章の点 × 役割の重み。章の点 ＝ ベクトルの点 ＋ 全文の点（それぞれ、比率 × 1/(60＋順位)）")
                rk = lambda r: f"{r}位" if r else "圏外（上位100に入らず）"
                for e in p["evidence"]:
                    code, title, _label, role = service.doc_line(ctx, e)
                    st.markdown(f"**{code}**　{title}（{role}）　{'人の点数に入る' if e['counted'] else '参考（点数には入らない）'}")
                    st.markdown(f"- <span class='v'>ベクトル</span>：{rk(e['vec_rank'])} → {e['vec_part']:.4f}"
                                f"　／　<span class='f'>全文</span>：{rk(e['fts_rank'])} → {e['fts_part']:.4f}"
                                f"　／　章の点 {e['section_score']:.4f} × 役割の重み {e['role_weight']:.2f} ＝ 文書の点 {e['score']:.4f}",
                                unsafe_allow_html=True)
            k = service.person_knowledge(ctx, p["emp"], hit_docs=[e["doc"] for e in p["evidence"]])
            with st.expander(f"紐づくナレッジ（文書 {len(k['docs'])}件／キャリアシート {len(k['career'])}項目）"):
                hits = {e["doc"]: e for e in p["evidence"]}
                for d in k["docs"]:
                    c1, c2 = st.columns([5, 1])
                    c1.markdown(f"{'★ ' if d['hit'] else ''}📄 {d['title']}　{d['code']}／{d['label']}／{d['role']}／{d['date']}")
                    if c2.button("中身を見る", key=f"kn|{p['emp']}|{d['doc_id']}"):
                        open_document(d["doc_id"], hit=hits.get(d["doc_id"]))
                st.markdown("キャリアシート：" + ("、".join(k["career"]) if k["career"] else "なし"))
                st.caption("★は、今回の相談に当たった文書です。")
            show_brief(p["emp"], query, [e["doc"] for e in p["evidence"]])

        groups = [("実績のある人", "完了したPJや採択された提案で関わった人", "proven", "track"),
                  ("隠れた杭", "不採択・保留・審査中の提案などから見つかった人", "hidden", "hidden")]
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
