# =============================================================
# _test/tokenizer/app.py — 開発用：形態素解析（search/tokenizer.py）の結果を確かめる画面
#
# 起動（data-triangle フォルダで実行）：streamlit run _test/tokenizer/app.py
#
# 確かめ方は2つ：
#   ・文章を入力する … その場で書いた文章を解析する（DBは使わない）
#   ・DBの章から選ぶ … 文書IDを入れると、DBに入っている章の本文を解析する
#                      接続は register/db.py と同じ（data-triangle フォルダ直下の .env を読む）
# 必要なもの：pip install sudachipy sudachidict_core streamlit pandas supabase python-dotenv
# =============================================================
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]   # data-triangle フォルダ
sys.path.insert(0, str(ROOT))
from search import tokenizer as tk           # noqa: E402

KIND_LABELS = {"compound": "複合語", "word": "単独の語", "part": "複合語の部分", "code": "型番など"}


@st.cache_resource
def get_db():
    from register import db
    return db.connect()


def show(text):
    """1つの文章について、取り出した語と、検索で使う形を並べて出す"""
    terms = tk.terms(text)
    left, right = st.columns(2)
    with left:
        st.markdown("**取り出した語**")
        st.dataframe(pd.DataFrame([{"語": t.text, "種類": KIND_LABELS[t.kind]} for t in terms]),
                     hide_index=True)
    with right:
        st.markdown("**キーワード（画面の1段目に出す）**")
        st.write("、".join(tk.keywords(text)) or "（なし）")
        st.markdown("**全文検索の列に入れる文字列**")
        st.code(tk.index_text(text), language=None)
        st.markdown("**検索の条件（PGroonga に渡す）**")
        st.code(tk.query_text(text), language=None)


st.set_page_config(page_title="形態素解析の確認", page_icon="🛠", layout="wide")
st.title("🛠 形態素解析の確認（開発用）")
st.caption("文書を登録するときと検索するときに、どの語が取り出されるかを確かめる画面です。")

mode = st.radio("確かめ方", ["文章を入力する", "DBの章から選ぶ"], horizontal=True)

if mode == "文章を入力する":
    text = st.text_area("確かめたい文章",
                        "第二工場の設備保全課で、研削盤の打合せの内容をシュミレーションした。型番はXYZ-9982。",
                        height=120)
    if text.strip():
        show(text)
else:
    doc_id = st.text_input("文書ID", "KZ-2023-0312")
    if st.button("DBから読む", type="primary"):
        try:
            rows = (get_db().table("document_sections")
                    .select("section_no, section_name, section_role, body")
                    .eq("doc_id", doc_id.strip()).order("section_no").execute().data)
        except Exception as e:  # .env がない、つながらない、など
            st.error(f"DBに接続できませんでした：{e}")
            rows = None
        if rows == []:
            st.warning("その文書IDの章は見つかりませんでした。")
        for r in rows or []:
            st.subheader(f"{r['section_no']}. {r['section_name']}（{r['section_role']}）")
            st.write(r["body"])
            show(r["body"])
            st.divider()