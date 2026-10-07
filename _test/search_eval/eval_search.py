# =============================================================
# _test/search_eval/eval_search.py — 新しいナレッジで、検索の精度を確かめる（v5）
#
# 評価セット v2（eval_queries.json）の11問で、3通りを比べる：
#   ・ベクトルのみ        … 章のベクトルと、質問文のベクトルの類似度
#   ・全文のみ            … PGroonga（SQL/v5_search_fts.sql を実行済みであること）
#   ・ハイブリッド(RRF)   … 2つの章の順位を RRF で統合
# どれも「章 → 文書 → 人」の集約（search/rank.py）を通して、人の順位で採点する。
# 各順位から取る章は、上位 retrieve.FTS_N（100）本まで。
#
# 使い方（リポジトリ直下で実行）：
#   python _test/search_eval/eval_search.py                        一覧を出す
#   python _test/search_eval/eval_search.py --detail Q1_remote     その問いの上位の中身も出す
#
# 前提：
#   ・DBに文書・章・embedding が入っている（initial_load/ の3つを実行済み）
#   ・.env に SUPABASE_URL、SUPABASE_KEY、OPENAI_API_KEY がある
#     （質問文のベクトル化は11回だけ。課金はごくわずか）
#   ・「全文のみ」「ハイブリッド」は、SQL/v5_search_fts.sql を SQL Editor で実行したあとに出る
#     実行していなくても、「ベクトルのみ」は出る
#
# 見方：
#   人MRR            … 正解の人が最初に出る順位の逆数の平均（1に近いほどよい）
#   人の再現率@5     … 正解の人のうち、上位5人に入った割合の平均
#   文書の再現率@10  … 正解の文書のうち、上位10文書に入った割合（正解の文書がある6問だけ）
#   負例の混入@10    … 上位に来てはいけない文書が、上位10文書に入った件数の合計
# =============================================================
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from register import db                       # noqa: E402
from search import embed, rank, retrieve      # noqa: E402

try:                                          # Windowsのコンソールで、変換できない文字があっても止めない
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

EVAL_FILE = ROOT / "_test" / "search_eval" / "eval_queries.json"
N = retrieve.FTS_N          # 各順位から取る章の数
TOP_DOCS = 10
TOP_PEOPLE = 5
METHODS = ["ベクトルのみ", "全文のみ", "ハイブリッド(RRF)"]


def fetch_all(sb, table, cols, order, page=1000):
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
    """順位づけに必要なデータをDBから読む"""
    sections, matrix = retrieve.load_section_vectors(sb)
    docs = fetch_all(sb, "documents",
                     "doc_id,doc_code,doc_type,result,pj_status,submitted_at,started_at", ["doc_id"])
    authors = fetch_all(sb, "document_authors", "doc_id,emp_id,role", ["doc_id", "emp_id"])
    emps = fetch_all(sb, "employees", "emp_id,emp_no,dept_id,is_active", ["emp_id"])
    corpus = rank.Corpus.from_rows(sections[["section_id", "doc_id"]].to_dict("records"),
                                   docs, authors, emps)
    return {
        "sections": sections, "matrix": matrix, "corpus": corpus,
        "doc_code": {d["doc_id"]: d["doc_code"] for d in docs},
        "emp_no": {e["emp_id"]: e["emp_no"] for e in emps},
        "emp_id_of": {e["emp_no"]: e["emp_id"] for e in emps},
    }


def section_scores(sb, ctx, text, warnings):
    """質問文から、方法ごとの {章ID: スコア} を作る"""
    qvec = embed.embed_query(text)
    hits = retrieve.vector_search(qvec, ctx["sections"], ctx["matrix"], k=N)
    vec = hits["section_id"].tolist()
    out = {"ベクトルのみ": rank.rrf([vec])}
    try:
        fts = retrieve.fulltext_search(sb, text, n=N)
    except Exception as e:                      # 関数がまだない、権限がない、など
        warnings.add(str(e)[:300])
        return out
    out["全文のみ"] = rank.rrf([fts]) if fts else {}
    out["ハイブリッド(RRF)"] = retrieve.hybrid_scores(vec, fts)
    return out


def score_query(scores, q, ctx):
    corpus = ctx["corpus"]
    searcher = ctx["emp_id_of"].get(q.get("searcher_emp"))
    res = rank.rank_people(scores, corpus, searcher=searcher, top_n=10 ** 6)
    people = sorted(res["proven"] + res["hidden"], key=lambda p: -p["score"])
    people_no = [ctx["emp_no"][p["emp"]] for p in people]
    exp = set(q["expected_emp"])
    first = next((i for i, e in enumerate(people_no, 1) if e in exp), None)

    best = rank.doc_scores(scores, corpus)
    docs = [ctx["doc_code"][d] for d, _ in sorted(best.items(), key=lambda kv: -kv[1][0])]
    exp_docs, neg_docs = set(q["expected_docs"]), set(q["negative_docs"])
    top_docs = docs[:TOP_DOCS]
    return {
        "rr": 1 / first if first else 0.0,
        "first": first,
        "rec5": len(exp & set(people_no[:TOP_PEOPLE])) / len(exp) if exp else None,
        "doc_rec": (len(exp_docs & set(top_docs)) / min(len(exp_docs), TOP_DOCS)) if exp_docs else None,
        "neg": len(neg_docs & set(top_docs)),
        "people": people[:TOP_PEOPLE], "people_no": people_no[:TOP_PEOPLE], "top_docs": top_docs,
    }


def show_detail(method, q, r, ctx):
    print(f"\n--- {q['id']} / {method} ---")
    print(f"質問：{q['text']}")
    exp_docs, neg_docs = set(q["expected_docs"]), set(q["negative_docs"])
    print(f"上位{TOP_DOCS}文書：")
    for i, d in enumerate(r["top_docs"], 1):
        mark = "○ 正解" if d in exp_docs else ("× 負例" if d in neg_docs else "")
        print(f"  {i:>2}. {d:<16}{mark}")
    print(f"上位{TOP_PEOPLE}人：")
    for i, (p, no) in enumerate(zip(r["people"], r["people_no"]), 1):
        mark = "★ 正解" if no in set(q["expected_emp"]) else ""
        ev = p["evidence"][0]
        print(f"  {i}. {no:>6}  {ev['role']}  {ctx['doc_code'][ev['doc']]:<16}{mark}")


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    detail = sys.argv[sys.argv.index("--detail") + 1] if "--detail" in sys.argv else None
    queries = json.loads(EVAL_FILE.read_text(encoding="utf-8"))["queries"]
    sb = db.connect()
    ctx = load(sb)
    sections = ctx["sections"]
    print(f"章 {len(sections)} 本 / 文書 {len(ctx['doc_code'])} 件 / 社員 {len(ctx['emp_no'])} 名を読みました。")
    models = sections["embedding_model"].unique().tolist()
    if models != [embed.MODEL]:
        print(f"注意：章のモデル（{models}）と、質問文のモデル（{embed.MODEL}）が一致していません。")

    warnings, results = set(), {m: {} for m in METHODS}
    for q in queries:
        for method, scores in section_scores(sb, ctx, q["text"], warnings).items():
            r = score_query(scores, q, ctx)
            results[method][q["id"]] = r
            if detail == q["id"]:
                show_detail(method, q, r, ctx)
    if detail and detail not in {q["id"] for q in queries}:
        print(f"\n--detail の問いが見つかりません：{detail}")

    print("\n=== まとめ ===")
    print(f"{'方法':<18}{'人MRR':>8}{'人の再現率@5':>14}{'文書の再現率@10':>16}{'負例の混入@10':>14}")
    for m in METHODS:
        rs = list(results[m].values())
        if not rs:
            print(f"{m:<18}   （全文検索の関数を呼べなかったため、出していません）")
            continue
        print(f"{m:<18}{mean(r['rr'] for r in rs):>8.3f}{mean(r['rec5'] for r in rs):>14.3f}"
              f"{mean(r['doc_rec'] for r in rs):>16.3f}{sum(r['neg'] for r in rs):>14d}")

    print("\n=== 問いごとの、正解の人が最初に出る順位（空は、どこにも出なかった） ===")
    print(f"{'問い':<24}" + "".join(f"{m:>18}" for m in METHODS))
    for q in queries:
        cells = []
        for m in METHODS:
            r = results[m].get(q["id"])
            cells.append("-" if r is None else (str(r["first"]) if r["first"] else ""))
        print(f"{q['id']:<24}" + "".join(f"{c:>18}" for c in cells))

    if warnings:
        print("\n全文検索を呼べませんでした：")
        for w in warnings:
            print(f"  {w}")
        print("  → SQL/v5_search_fts.sql を SQL Editor で実行したか、確かめてください。")


if __name__ == "__main__":
    main()
