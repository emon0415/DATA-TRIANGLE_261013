# =============================================================
# _test/rank/app.py — 開発用：比重を動かして、人の順位がどう変わるかを確かめる画面
#
# 起動：streamlit run _test/rank/app.py（どのフォルダからでもよい）
#
# サイドバーで動かせるもの：
#   ・全文検索の比重    0＝ベクトルだけ ／ 1＝全文だけ ／ 0.5＝同じ重み（既定）
#   ・役割ごとの重み    重視 1.0 ／ ふつう 0.6 ／ あまり考慮しない 0.3
#   ・経験の厚み（β）、新しさ、同じ部署が続いたときの割り引き
#   ・絞り込み（部署、年）、検索した本人の除外
# 画面の下に、評価セット11問での点数が、既定の設定と並んで出る。
# 動かした設定が、評価セットの点数をどう変えるかを見ながら、既定の値を決めるための画面。
#
# 前提：
#   ・DBに文書・章・embedding が入っている
#   ・.env に SUPABASE_URL、SUPABASE_KEY、OPENAI_API_KEY がある
#   ・全文検索は SQL/v5_search_fts.sql を実行したあと（なければ、ベクトルだけで計算する）
# 質問文をベクトルにするごとに、OpenAI の課金がある（ごくわずか。同じ質問文は使い回す）
# =============================================================
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]            # リポジトリ直下
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_test" / "search_eval"))
from register import db                               # noqa: E402
from search import rank, retrieve                     # noqa: E402
import eval_search as ev                              # noqa: E402

EVAL_FILE = ROOT / "_test" / "search_eval" / "eval_queries.json"
QUERIES = json.loads(EVAL_FILE.read_text(encoding="utf-8"))["queries"]
SAMPLES = {q["text"]: q for q in QUERIES}

DEFAULTS = {"w_fts": 0.5, "beta": 0.3, "recency": 0.0, "diversity": 0.0}
ROLES = list(rank.DEFAULT_ROLE_LEVELS)


# ---------- DBと、質問文ごとの順位（同じ質問文は使い回す） ----------
@st.cache_resource
def get_ctx():
    sb = db.connect()
    return sb, ev.load(sb)


@st.cache_data(show_spinner=False)
def get_rankings(text):
    sb, ctx = get_ctx()
    return ev.rankings(sb, ctx, text)


def combine(vec, fts, w_fts):
    """ベクトルと全文の順位を、比重をつけて統合する。全文を呼べないときは、ベクトルだけ"""
    if fts is None:
        return rank.rrf([vec])
    return retrieve.hybrid_scores(vec, fts, w_vector=1 - w_fts, w_fts=w_fts)


# ---------- サイドバー ----------
def reset():
    for key, value in DEFAULTS.items():
        st.session_state[key] = value
    for role, level in rank.DEFAULT_ROLE_LEVELS.items():
        st.session_state[f"role_{role}"] = level


def init_state():
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value)
    for role, level in rank.DEFAULT_ROLE_LEVELS.items():
        st.session_state.setdefault(f"role_{role}", level)


def sidebar(ctx, fts_ok):
    sb = st.sidebar
    sb.button("既定に戻す", on_click=reset)

    sb.header("検索の比重")
    sb.slider("全文検索の比重", 0.0, 1.0, step=0.1, key="w_fts", disabled=not fts_ok,
              help="0＝ベクトル（意味の近さ）だけ ／ 1＝全文（言葉の一致）だけ ／ 0.5＝同じ重み")
    sb.caption("ベクトル 0.0 ← → 1.0 全文　（0.5＝同じ重み）" if fts_ok
               else "全文検索の関数を呼べないため、ベクトルだけで計算しています")

    sb.header("役割の重み")
    levels = {role: sb.select_slider(role, options=list(rank.ROLE_LEVELS), key=f"role_{role}")
              for role in ROLES}
    sb.caption("　／　".join(f"{k}＝{v}" for k, v in rank.ROLE_LEVELS.items()))

    sb.header("そのほか")
    beta = sb.slider("経験の厚み（β）", 0.0, 1.0, step=0.1, key="beta",
                     help="2番目に強い文書を、βの割合で加点する。0なら、最も強い1件だけで決まる")
    recency = sb.slider("新しさ", 0.0, 0.2, step=0.05, key="recency",
                        help="1年古いごとに、この割合だけ割り引く（最大5年）。0なら無効")
    diversity = sb.slider("多様性", 0.0, 0.5, step=0.1, key="diversity",
                          help="同じ部署の人が続くたびに、この割合だけ割り引く。0なら無効")

    sb.header("絞り込み")
    dept_ids = sorted(ctx["dept_name"], key=lambda d: ctx["dept_name"][d])
    depts = sb.multiselect("部署（人の今の所属）", dept_ids, format_func=lambda d: ctx["dept_name"][d])
    years = sorted({int(str(d["date"])[:4]) for d in ctx["corpus"].docs.values() if d["date"]})
    year_sel = sb.multiselect("年（文書の日付）", years)
    searcher_no = sb.text_input("検索する人の社員番号（結果から除く）")
    top_n = sb.slider("表示する人数", 3, 10, 5)

    return {
        "levels": levels, "w_fts": st.session_state["w_fts"] if fts_ok else 0.0,
        "beta": beta, "recency": recency, "diversity": diversity,
        "depts": depts, "years": year_sel, "top_n": top_n,
        "searcher": ctx["emp_id_of"].get(searcher_no.strip()) if searcher_no.strip() else None,
        "searcher_unknown": bool(searcher_no.strip()) and searcher_no.strip() not in ctx["emp_id_of"],
    }


def rank_kwargs(p):
    """評価セットの採点に使う順位づけの設定（絞り込みと検索者は、採点には使わない）"""
    return {"role_weight": rank.role_weights(p["levels"]), "beta": p["beta"],
            "recency": p["recency"], "diversity": p["diversity"]}


# ---------- 結果の表示 ----------
def show_people(res, ctx, expect, top_n):
    cols = st.columns(2)
    for col, key, title in ((cols[0], "proven", "実績のある人（完了PJ・採択の提案）"),
                            (cols[1], "hidden", "隠れた杭（それ以外）")):
        with col:
            st.subheader(title)
            if not res[key]:
                st.caption("該当する人はいません")
            for i, p in enumerate(res[key], 1):
                no = ctx["emp_no"][p["emp"]]
                star = "　★ 正解" if expect and no in set(expect["expected_emp"]) else ""
                st.markdown(f"**{i}. {ctx['emp_name'][p['emp']]}**（{no}／{ctx['dept_name'][p['dept_id']]}）"
                            f"　スコア {p['score']:.4f}{star}")
                for e in p["evidence"]:
                    code = ctx["doc_code"][e["doc"]]
                    mark = ""
                    if expect:
                        mark = " ○" if code in set(expect["expected_docs"]) else (
                            " ×負例" if code in set(expect["negative_docs"]) else "")
                    st.caption(f"{e['role']}｜{code}{mark}｜{ctx['doc_title'][e['doc']]}｜{e['score']:.4f}")


def evaluate(ctx, w_fts, kwargs):
    rows = {}
    for q in QUERIES:
        vec, fts, _ = get_rankings(q["text"])
        rows[q["id"]] = ev.score_query(combine(vec, fts, w_fts), q, ctx, **kwargs)
    return rows


def summary(rows):
    rs = list(rows.values())
    return {"人MRR": round(ev.mean(r["rr"] for r in rs), 3),
            "人の再現率@5": round(ev.mean(r["rec5"] for r in rs), 3),
            "文書の再現率@10": round(ev.mean(r["doc_rec"] for r in rs), 3),
            "負例の混入@10": sum(r["neg"] for r in rs)}


def show_evaluation(ctx, p):
    st.divider()
    st.subheader("評価セット11問での点数")
    with st.spinner("評価セットを準備しています（初回だけ、質問文11回分のベクトル化）"):
        base = evaluate(ctx, DEFAULTS["w_fts"],
                        {"role_weight": rank.ROLE_WEIGHT, "beta": DEFAULTS["beta"],
                         "recency": DEFAULTS["recency"], "diversity": DEFAULTS["diversity"]})
        now = evaluate(ctx, p["w_fts"], rank_kwargs(p))
    st.dataframe(pd.DataFrame([{"設定": "既定", **summary(base)}, {"設定": "今の設定", **summary(now)}]),
                 hide_index=True)

    def first(r):
        return str(r["first"]) if r["first"] else "（出ない）"
    table = pd.DataFrame([{"問い": q["id"], "既定": first(base[q["id"]]), "今の設定": first(now[q["id"]])}
                          for q in QUERIES])
    st.caption("問いごとの、正解の人が最初に出る順位")
    st.dataframe(table, hide_index=True)
    st.caption("問いは11問なので、1問の順位が変わるだけで点数は大きく動きます。点数に合わせすぎず、"
               "上の結果と見比べながら決めてください。")


# ---------- 画面 ----------
def main():
    st.set_page_config(page_title="比重の確認", page_icon="🛠", layout="wide")
    st.title("🛠 比重の確認（開発用）")
    init_state()
    try:
        _, ctx = get_ctx()
    except Exception as e:
        st.error(f"DBから読めませんでした：{e}")
        st.stop()

    # 全文検索を呼べるかは、評価セットの最初の問いで確かめる
    _, probe_fts, probe_err = get_rankings(QUERIES[0]["text"])
    fts_ok = probe_fts is not None
    if not fts_ok:
        st.warning(f"全文検索を呼べませんでした。ベクトルだけで計算します。（{probe_err}）\n\n"
                   "SQL/v5_search_fts.sql を SQL Editor で実行したか、確かめてください。")
    p = sidebar(ctx, fts_ok)
    if p["searcher_unknown"]:
        st.sidebar.warning("その社員番号は見つかりません。除外しません。")

    pick = st.selectbox("質問文の例", list(SAMPLES) + ["（自分で入力する）"])
    text = st.text_area("質問文", "" if pick.startswith("（") else pick, height=80)
    if st.button("検索する", type="primary") and text.strip():
        st.session_state["query"] = text.strip()

    query = st.session_state.get("query")
    if query:
        vec, fts, _ = get_rankings(query)
        scores = combine(vec, fts, p["w_fts"])
        res = rank.rank_people(
            scores, ctx["corpus"], searcher=p["searcher"], depts=p["depts"] or None, years=p["years"] or None,
            top_n=p["top_n"], **rank_kwargs(p))
        st.caption(f"質問文：{query}")
        show_people(res, ctx, SAMPLES.get(query), p["top_n"])
    else:
        st.info("質問文を選んで「検索する」を押すと、結果が出ます。設定を動かすと、結果もすぐ変わります。")

    if st.checkbox("評価セット11問での点数を出す", value=True):
        show_evaluation(ctx, p)


main()
