"""
登録タブの画面：Word文書をアップロードすると、チェックしてからDBに登録し、章を埋め込む
登録のあと、関係者の看板（profiles）を作り直し、検索のキャッシュを消す（すぐ検索に出るように）
単体で起動：streamlit run register/page.py
app.py から呼ぶ：from register.page import show → show()
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))  # app.py から呼ばれても db.py などを読めるように
from db import connect, existing_docs, load_masters, save_document  # noqa: E402
from embed import embed_document  # noqa: E402
from word_to_rows import check_duplicates, read_word  # noqa: E402
from search import profiles_store  # noqa: E402  （search/ は db.py が読めるようにしている）

TYPE_NAMES = {"proposal": "改善提案", "project": "PJ文書"}


@st.cache_resource
def get_db():
    return connect()


@st.cache_data(ttl=600)
def get_masters():
    return load_masters(get_db())


def check_files(files):
    """アップロードされたファイルを読んで、チェックの結果と登録済みの文書を返す"""
    masters = get_masters()
    results = {f.name: read_word(f.getvalue(), f.name, masters) for f in files}
    doc_codes = [d["doc_code"] for rows, _, _ in results.values() for d in rows["documents"]]
    existing = existing_docs(get_db(), doc_codes)
    for filename, (more_problems, more_notes) in check_duplicates(results, existing).items():
        results[filename][1].extend(more_problems)
        results[filename][2].extend(more_notes)
    return results, existing


def show_file(filename, rows, problems, notes):
    doc = rows["documents"][0] if rows["documents"] else None
    icon = "❌" if problems else "⚠️" if notes else "✅"
    label = f"{icon} {filename}"
    if doc:
        label += f"　{doc['title'] or ''}"
    with st.expander(label, expanded=bool(problems or notes)):
        for p in problems:
            st.error(p)
        for n in notes:
            st.warning(n)
        if doc:
            st.write(f"{doc['doc_code']}／{TYPE_NAMES[doc['doc_type']]}／"
                     f"章 {len(rows['document_sections'])}件／関係者 {len(rows['document_authors'])}人")
            st.dataframe({
                "章": [s["section_name"] for s in rows["document_sections"]],
                "章の役割": [s["section_role"] for s in rows["document_sections"]],
                "本文（冒頭）": [s["body"][:40] + "…" for s in rows["document_sections"]],
            }, hide_index=True)


def register(results):
    sb = get_db()
    done = []
    with st.status("登録しています", expanded=True) as status:
        for filename, (rows, _, _) in results.items():
            doc_code = rows["documents"][0]["doc_code"]
            try:
                r = save_document(sb, rows)  # 振られた doc_id が rows の章にも入る
                st.write(f"✓ {doc_code}：文書・章・関係者と、キーワード {r['keywords']}件をデータベースに登録しました")
                n = embed_document(sb, rows)
                st.write(f"✓ {doc_code}：章 {n}件を埋め込みました")
                p = profiles_store.rebuild(sb, emp_ids=r["emp_ids"])
                st.write(f"✓ {doc_code}：関係者 {p['people']}人の看板を更新しました")
                done.append(doc_code)
            except Exception as e:  # 1件失敗しても、残りは続ける
                st.write(f"✗ {doc_code}：失敗しました（{e}）")
        # 成功したときは、このあと画面を切り替えて「登録しました」を出すので、ここでは完了にしない
        # （完了にすると、切り替えの間「登録しました」のまま処理中の表示が続いてしまう）
        if done:
            # 「人を探す」「仲間を探す」は起動時に読んだデータをキャッシュしている。
            # 消しておくと、次に開いたときにDBから読み直し、登録した文書がすぐ検索に出る
            st.cache_resource.clear()
            st.cache_data.clear()
        failed = len(results) - len(done)
        if failed:
            status.update(label=f"{failed}件が失敗しました。同じファイルで、もう一度登録してください",
                          state="error")
    return done


def show():
    st.title("文書を登録する")
    st.caption("提案書やPJ文書（Word）をアップロードすると、章ごとに分けてデータベースに登録します。")

    # 登録が終わったら、アップロード欄を空に戻すために key を変える
    st.session_state.setdefault("upload_key", 0)
    files = st.file_uploader("Word文書（.docx）", type=["docx"], accept_multiple_files=True,
                             key=f"upload_{st.session_state.upload_key}")

    if st.session_state.get("registered"):
        st.success(f"登録しました：{'、'.join(st.session_state.registered)}")
    if not files:
        return
    st.session_state.pop("registered", None)

    try:
        results, existing = check_files(files)
    except Exception as e:
        st.error(f"データベースにつながりませんでした（{e}）")
        return

    for filename, (rows, problems, notes) in results.items():
        show_file(filename, rows, problems, notes)

    bad = [f for f, (_, problems, _) in results.items() if problems]
    if bad:
        st.error(f"❌ の文書を直すか外すと、登録できます（{len(bad)}件）")
        return
    overwrite = True
    if existing:
        overwrite = st.checkbox(f"登録済みの文書（{'、'.join(sorted(existing))}）を上書きする")
    if st.button(f"{len(results)}件をデータベースに登録する", type="primary", disabled=not overwrite):
        done = register(results)
        if len(done) == len(results):
            st.session_state.registered = done
            st.session_state.upload_key += 1
            st.rerun()


if __name__ == "__main__":
    st.set_page_config(page_title="文書を登録する", page_icon="📄", layout="wide")
    show()
