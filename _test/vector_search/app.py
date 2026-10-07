# =============================================================
# _test/vector_search/app.py — 開発用：ベクトル検索（search/retrieve.py）の結果を確かめる画面
#
# 起動：streamlit run _test/vector_search/app.py（どのフォルダからでもよい）
#
# 前提：
#   ・document_sections の embedding が入っていること（initial_load/embed_sections.py）
#   ・data-triangle フォルダ直下の .env に SUPABASE_URL、SUPABASE_KEY、OPENAI_API_KEY
#   ・質問文を1回ベクトルにするごとに OpenAI の課金がある（ごくわずか）
# 必要なもの：pip install streamlit pandas numpy openai supabase python-dotenv
# =============================================================
import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]   # data-triangle フォルダ
sys.path.insert(0, str(ROOT))
from register import db                      # noqa: E402
from search import embed, retrieve           # noqa: E402

# 試しやすい質問文と、上位に来てほしい文書／来てほしくない文書（評価セット v2 から読む）
#   文書は文書番号（doc_code）で書いてある。人の正解（expected_emp）は、この画面では使わない
EVAL_FILE = ROOT / "_test" / "search_eval" / "eval_queries.json"
SAMPLES = {
    q["text"]: {"期待": q["expected_docs"], "負例": q["negative_docs"]}
    for q in json.loads(EVAL_FILE.read_text(encoding="utf-8"))["queries"]
}


@st.cache_resource
def load():
    """章とベクトルは1回だけ読む（画面を操作するたびに読み直さない）"""
    return retrieve.load_section_vectors(db.connect())


@st.cache_data(show_spinner=False)
def query_vector(text):
    """同じ質問文は使い回して、課金を増やさない"""
    return embed.embed_query(text)


def mark(doc_code, expect):
    if doc_code in expect["期待"]:
        return "✅ 期待"
    if doc_code in expect["負例"]:
        return "⚠️ 負例"
    return ""


st.set_page_config(page_title="ベクトル検索の確認", page_icon="🛠", layout="wide")
st.title("🛠 ベクトル検索の確認（開発用）")

try:
    sections, matrix = load()
except Exception as e:
    st.error(f"DBから章を読めませんでした：{e}")
    st.stop()
models = sections["embedding_model"].unique().tolist()
st.caption(f"章 {len(sections)} 本（文書 {sections.doc_id.nunique()} 件）を読み込みました。埋め込みのモデル：{'、'.join(models)}")
if models != [embed.MODEL]:
    st.warning(f"章のモデルと、質問文のモデル（{embed.MODEL}）が一致していません。比べても意味がありません。")

pick = st.selectbox("質問文の例", list(SAMPLES) + ["（自分で入力する）"])
text = st.text_area("質問文", "" if pick.startswith("（") else pick, height=80)
k = st.slider("表示する章の数", 10, 100, 20, step=10)
expect = SAMPLES.get(text.strip(), {"期待": [], "負例": []})

if st.button("検索する", type="primary") and text.strip():
    try:
        qvec = query_vector(text.strip())
    except Exception as e:
        st.error(f"質問文をベクトルにできませんでした：{e}")
        st.stop()
    hits = retrieve.vector_search(qvec, sections, matrix, k=len(sections))
    docs = retrieve.to_documents(hits)
    if expect["期待"] or expect["負例"]:
        docs["確認"] = docs.doc_code.map(lambda d: mark(d, expect))
        hits["確認"] = hits.doc_code.map(lambda d: mark(d, expect))
        found = docs[docs["確認"] != ""][["doc_code", "rank", "確認"]]
        st.subheader("期待・負例の文書の順位（文書の単位）")
        st.dataframe(found, hide_index=True)

    left, right = st.columns(2)
    with left:
        st.subheader(f"文書の上位 {min(k, len(docs))} 件")
        st.dataframe(docs.drop(columns=["doc_id"]).head(k), hide_index=True)
    with right:
        st.subheader(f"章の上位 {k} 本")
        view = hits.head(k).assign(本文=lambda d: d.body.str.slice(0, 60))
        st.dataframe(view.drop(columns=["body", "section_id", "doc_id", "embedding_model"]), hide_index=True)