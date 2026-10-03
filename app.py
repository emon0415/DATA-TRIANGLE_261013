"""
隠れ出る杭検索：画面のモック（DB・OpenAIにはつながない。表示はすべてダミー）
起動：streamlit run app.py
"""
import time
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
</style>
""", unsafe_allow_html=True)

# ---------- ダミーデータ（B1の例） ----------
PEOPLE = [
    {"id": "10741", "name": "相川 涼", "dept": "技術開発部", "kind": "track",
     "keywords": ["稼働データ", "電流値", "通信ユニット", "停止の記録"],
     "docs": [("PJ-2022-0018", "稼働データ収集基盤の試験導入プロジェクト", "PJ・完了", "主担当")],
     "reason": "装置の稼働データを集め、停止の前に表れる変化を確かめる試験を主担当として進めています（PJ-2022-0018）。"
               "「故障する前に異常に気づく」という相談に対して、データの集め方と、実際に停止の数時間前に変化が見られた事例を具体的に話せる人です。"},
    {"id": "2248", "name": "横山 麻衣", "dept": "第一工場 設備保全課", "kind": "track",
     "keywords": ["旋盤", "ベアリング", "温度の記録", "交換の目安"],
     "docs": [("KZ-2024-0163", "旋盤における異常の事前把握の検討について", "提案・採択", "提案者")],
     "reason": "旋盤のベアリングの周辺の温度を毎日記録し、焼き付く前に交換する取り組みを提案し、採択されています（KZ-2024-0163）。"
               "現場ですぐに始められる、費用の小さい方法の経験があります。"},
    {"id": "24518", "name": "谷口 実", "dept": "第四工場 生産技術課", "kind": "hidden",
     "keywords": ["異常の兆候", "設備の停止", "事前の把握"],
     "docs": [("KZ-2025-0644", "設備の異常兆候を事前に捉える仕組みの導入について", "提案・不採択", "提案者")],
     "reason": "設備の異常を停止の前に捉える仕組みを提案しています（KZ-2025-0644）。不採択でしたが、問題の捉え方は今回の相談とほぼ同じです。"
               "キーワードでは見つからない言い回しで書かれているため、これまで表に出ていなかった可能性があります。"},
    {"id": "20874", "name": "井上 健一", "dept": "第二工場 生産技術課", "kind": "hidden",
     "keywords": ["不良の予兆", "データの収集", "検討の手順"],
     "docs": [("KZ-2023-0312", "振動データによる不良予兆検知の検討について", "提案・保留", "提案者")],
     "reason": "設備のデータから不良の予兆を捉える検討を提案しています（KZ-2023-0312）。保留のままですが、"
               "本社の稼働データのPJ（PJ-2022-0018）と目的が近く、両者をつなぐと検討を前に進められます。"},
    {"id": "2137", "name": "小島 千鶴", "dept": "第一工場 設備保全課", "kind": "hidden",
     "keywords": ["研削盤", "電流値", "ガイドレール"],
     "docs": [("KZ-2023-0091", "研削盤における異常の事前把握の検討について", "提案・審査中", "提案者")],
     "reason": "研削盤の駆動部の電流値を毎日記録し、停止の前に点検する方法を提案しています（KZ-2023-0091）。"
               "記録を取るだけで始められる、現場向けの方法を知っています。"},
]
DEPTS = ["経営企画部", "情報システム部", "調達部", "品質保証部", "技術開発部", "生産技術部", "営業技術部",
         "国内営業部", "海外営業部", "カスタマーサービス部",
         "大阪支店 営業課", "大阪支店 サービス課", "名古屋支店 営業課", "名古屋支店 サービス課", "福岡営業所 営業課"] + \
        [f"{f} {k}" for f in ("第一工場", "第二工場", "第三工場", "第四工場")
         for k in ("生産技術課", "製造課", "設備保全課", "品質保証課")]

# ---------- 画面の切り替え ----------
page = st.sidebar.radio("画面", ["人を探す", "文書を登録する"], label_visibility="collapsed")

# ========== 人を探す ==========
if page == "人を探す":
    with st.sidebar:
        st.subheader("条件")
        depts = st.multiselect("部署", DEPTS, placeholder="すべての部署")
        st.slider("年", 2016, 2026, (2016, 2026))

    st.title("隠れ出る杭検索")
    st.markdown('<p class="lead">困っていることを書くと、似た経験を持つ人を社内の文書から探します。</p>',
                unsafe_allow_html=True)
    q = st.text_area("相談したいこと", "設備が故障する前に異常に気づける仕組みを作りたい", height=90)
    if st.button("人を探す", type="primary"):
        st.session_state.searched = True
        st.session_state.reasons = set()

    if st.session_state.get("searched"):
        def card(p):
            kw = "".join(f'<span class="kw">{k}</span>' for k in p["keywords"])
            st.markdown(f'<div class="person {p["kind"]}"><h4>{p["name"]}</h4>'
                        f'<div class="dept">{p["dept"]}</div><div>{kw}</div></div>', unsafe_allow_html=True)
            for doc_id, title, status, role in p["docs"]:
                st.markdown(f"📄 [{title}](#{doc_id})　{doc_id}／{status}／{role}")
            if p["id"] in st.session_state.reasons:
                st.info(p["reason"])
            elif st.button("推薦理由を見る", key=f"r{p['id']}"):
                with st.spinner("根拠の文書から推薦理由を書いています"):
                    time.sleep(1.2)
                st.session_state.reasons.add(p["id"])
                st.rerun()

        groups = [("実績のある人", "完了したPJや採択された提案で関わった人", "track"),
                  ("隠れた杭", "不採択・保留・審査中の提案などから見つかった人", "hidden")]
        cols = st.columns(len(groups))
        for col, (label, desc, kind) in zip(cols, groups):
            with col:
                st.subheader(label)
                st.caption(desc)
                hits = [p for p in PEOPLE if p["kind"] == kind and (not depts or p["dept"] in depts)]
                for p in hits:
                    card(p)
                    st.divider()
                if not hits:
                    st.write("選んだ部署には該当する人がいません。部署の条件を外すと表示されます。")
        st.caption("※この画面はモックです。表示している人と文書はダミーです。")
    else:
        st.info("相談したいことを書いて「人を探す」を押してください。")

# ========== 文書を登録する ==========
else:
    st.title("文書を登録する")
    st.markdown('<p class="lead">提案書やPJ文書（Word）をアップロードすると、章ごとに分けてデータベースに登録します。</p>',
                unsafe_allow_html=True)
    files = st.file_uploader("Word文書（.docx）", type=["docx"], accept_multiple_files=True)
    names = [f.name for f in files] if files else []
    if not names and not st.session_state.get("sample") and st.button("サンプル文書で試す"):
        st.session_state.sample = True
    if st.session_state.get("sample") and not names:
        names = ["KZ-2025-0644.docx"]

    if names:
        st.write("登録する文書：", "、".join(names))
        if st.button("データベースに登録する", type="primary"):
            with st.status("登録しています", expanded=True) as s:
                for step in ["文書を読み込む", "章ごとに分けて、章の役割を付ける", "キーワードを抽出する（SudachiPy）",
                             "章を埋め込む（OpenAI）", "データベースに登録する（Supabase）", "関係者の看板を更新する"]:
                    st.write(f"✓ {step}")
                    time.sleep(0.6)
                s.update(label="登録しました", state="complete")
            st.subheader("登録した内容（例）")
            st.dataframe({
                "章": ["現状", "問題点", "提案内容", "期待効果"],
                "章の役割": ["背景", "課題意識", "行動案", "目標"],
                "キーワード": ["設備の停止", "異常の兆候", "事前の把握、点検の計画", "停止時間の短縮"],
                "埋め込み": ["済", "済", "済", "済"],
            }, hide_index=True)
            st.caption("登録先：documents、document_sections、document_authors、文書のキーワード。看板は画面には表示しません。")
