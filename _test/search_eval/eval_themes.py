# =============================================================
# _test/search_eval/eval_themes.py — テーマ別の36問で、検索の出方と足切りの初期値を確かめる
#
# 問い（eval_themes.json）：
#   ・横断テーマ10個×3問（言葉が近い／言い換え／場面から）…答えが決まっている（テーマの7文書とその著者）
#   ・範囲外6問（社員旅行、ラーメン…）…「該当なし」になるのが正しい
# 本番の画面と同じ search.service.search を通して採点するので、足切り・信頼度もそのまま見える。
#
# 使い方（リポジトリ直下で実行）：
#   python _test/search_eval/eval_themes.py                  一覧と、足切りの表を出す
#   python _test/search_eval/eval_themes.py --detail T01b    その問いの上位の中身も出す
#   python _test/search_eval/eval_themes.py --csv out.csv    結果をCSVにも書く
#
# 前提：DBに文書・章・embeddingが入っている。.env に SUPABASE_URL、SUPABASE_KEY、OPENAI_API_KEY がある
#       （質問のベクトル化は72回。課金はごくわずか）
#
# 見方：
#   当たり   … 上位5人に、正解の人（テーマの文書の著者）が1人でもいる
#   文書     … 上位5人の根拠の文書に、正解の文書が1件でもある
#   最初の順位 … 正解の人が最初に出る順位（上位5人の中）
#   最高cos  … 質問と、いちばん近い章のコサイン類似度（足切りの目安）
#   最高FTS  … 全文検索の、いちばん高いスコア
# =============================================================
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

QFILE = ROOT / "_test" / "search_eval" / "eval_themes.json"
VEC_STEPS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
TOP = 5


def judge(res, ctx, q):
    """1問の結果を採点する。res は service.search の戻り値"""
    people = sorted(res["proven"] + res["hidden"], key=lambda p: -p["score"])[:TOP]
    nos = [ctx["emps"][p["emp"]]["emp_no"] for p in people]
    exp, expd = set(q["expected_emp"]), set(q["expected_docs"])
    first = next((i for i, n in enumerate(nos, 1) if n in exp), None)
    got_docs = {ctx["docs"][e["doc"]]["doc_code"] for p in people for e in p["evidence"]}
    return {
        "none": bool(res["none"]),
        "hit": first is not None,
        "first": first,
        "doc_hit": bool(expd & got_docs),
        "conf": people[0]["confidence"] if people else "-",
        "people": people, "nos": nos,
    }


def probe(sb, ctx, text):
    """足切りをかける前の、最高のコサイン類似度と、最高の全文スコア"""
    from search import embed, retrieve
    qvec = embed.embed_query(text)
    v = retrieve.vector_search(qvec, ctx["sections"], ctx["matrix"], k=1)
    cos = float(v["score"].iloc[0]) if len(v) else 0.0
    try:
        pairs = retrieve.fulltext_search_scored(sb, text, n=retrieve.FTS_N)
        fts = max((s for _, s in pairs), default=0.0)
    except Exception:
        fts = None
    return cos, fts


def sweep(rows, fts_steps):
    """足切りを動かしたとき、「結果が出る」問いの数を数える。
    章は、コサインが vec_min 以上、または全文スコアが fts_min 以上なら点が付く。
    つまり「最高cos ≥ vec_min」または「最高FTS ≥ fts_min」なら、結果が出る（該当なしにならない）"""
    ins = [r for r in rows if r["expect"] == "hit"]
    outs = [r for r in rows if r["expect"] == "none"]
    print(f"\n=== 足切りの表（範囲内 {len(ins)}問で結果が出た数／範囲外 {len(outs)}問で「該当なし」になった数） ===")
    print("            " + "".join(f"fts≥{f:<6g}" for f in fts_steps))
    for v in VEC_STEPS:
        cells = []
        for f in fts_steps:
            ok_in = sum(1 for r in ins if r["cos"] >= v or (r["fts"] is not None and r["fts"] >= f))
            ok_out = sum(1 for r in outs if not (r["cos"] >= v or (r["fts"] is not None and r["fts"] >= f)))
            cells.append(f"{ok_in}/{len(ins)}・{ok_out}/{len(outs)}".ljust(11))
        print(f"cos≥{v:<6.2f}" + "".join(cells))
    print("読み方：「範囲内は多く、範囲外も多く」が理想（例 30/30・6/6）。範囲内が減り始めたら、足切りが厳しすぎ。")


def main():
    detail = sys.argv[sys.argv.index("--detail") + 1] if "--detail" in sys.argv else None
    csv_path = sys.argv[sys.argv.index("--csv") + 1] if "--csv" in sys.argv else None
    from register import db
    from search import service
    queries = json.loads(QFILE.read_text(encoding="utf-8"))["queries"]
    sb = db.connect()
    ctx = service.load(sb)
    print(f"章 {len(ctx['sections'])} 本 / 文書 {len(ctx['docs'])} 件 / 社員 {len(ctx['emps'])} 名を読みました。")
    print(f"足切りの既定値：ベクトル {service.DEFAULTS['vec_min']}／全文 {service.DEFAULTS['fts_min']}\n")

    rows = []
    print(f"{'問い':<6}{'種類':<10}{'結果':<8}{'当たり':<6}{'文書':<5}{'最初':<5}{'信頼度':<5}{'最高cos':>8}{'最高FTS':>9}  質問")
    for q in queries:
        res = service.search(sb, ctx, q["text"])
        j = judge(res, ctx, q)
        cos, fts = probe(sb, ctx, q["text"])
        r = {**{k: q[k] for k in ("id", "theme", "kind", "text", "expect")}, **j, "cos": cos, "fts": fts}
        rows.append(r)
        if q["expect"] == "hit":
            ok, doc = ("○" if j["hit"] else "×"), ("○" if j["doc_hit"] else "×")
            state = "該当なし" if j["none"] else "表示"
        else:
            ok = doc = "-"
            state = "○なし" if j["none"] else "×出た"
        ft = f"{fts:.2f}" if fts is not None else "n/a"
        print(f"{q['id']:<6}{q['kind']:<10}{state:<8}{ok:<6}{doc:<5}{str(j['first'] or '-'):<5}{j['conf']:<5}{cos:>8.3f}{ft:>9}  {q['text'][:28]}")
        if detail == q["id"]:
            for i, p in enumerate(j["people"], 1):
                e = ctx["emps"][p["emp"]]
                star = "★" if e["emp_no"] in set(q["expected_emp"]) else " "
                ev = p["evidence"][0]
                print(f"      {i}. {star} {e['emp_no']} {e['name']} [{p['frame']}] 点{p['score']:.4f} 信頼度{p['confidence']} "
                      f"cos{ev.get('vec_cos') or 0:.2f} {ctx['docs'][ev['doc']]['doc_code']}")

    ins = [r for r in rows if r["expect"] == "hit"]
    outs = [r for r in rows if r["expect"] == "none"]
    print("\n=== まとめ ===")
    print(f"範囲内 {len(ins)}問：当たり {sum(r['hit'] for r in ins)}／文書 {sum(r['doc_hit'] for r in ins)}／該当なしになった {sum(r['none'] for r in ins)}")
    for k in ("言葉が近い", "言い換え", "場面から"):
        g = [r for r in ins if r["kind"] == k]
        print(f"  {k}：当たり {sum(r['hit'] for r in g)}/{len(g)}")
    print(f"範囲外 {len(outs)}問：「該当なし」になった {sum(r['none'] for r in outs)}（残りは足切りを通ってしまった）")
    if ins:
        print(f"範囲内の最高cos：最小 {min(r['cos'] for r in ins):.3f}／中央 {sorted(r['cos'] for r in ins)[len(ins)//2]:.3f}")
    if outs:
        print(f"範囲外の最高cos：最大 {max(r['cos'] for r in outs):.3f}")
    fv = sorted({round(r['fts'], 2) for r in rows if r["fts"] is not None})
    if fv:
        fi = sorted(r["fts"] for r in ins if r["fts"] is not None); fo = sorted(r["fts"] for r in outs if r["fts"] is not None)
        print(f"最高FTS：範囲内 {fi[0]:.2f}〜{fi[-1]:.2f}／範囲外 {(fo[0] if fo else 0):.2f}〜{(fo[-1] if fo else 0):.2f}")
        cand = sorted({0, 1, 2, 3, 5, 8, 12, round(fo[-1], 1) if fo else 0, round(fi[0], 1)})
        sweep(rows, cand)
    else:
        print("全文検索のスコアが取れませんでした（SQL/v5_search_fts.sql が未実行かも）。ベクトルだけで見ます。")
        sweep(rows, [0])
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["id", "テーマ", "種類", "質問", "期待", "結果", "当たり", "文書", "最初の順位", "信頼度", "最高cos", "最高FTS"])
            for r in rows:
                w.writerow([r["id"], r["theme"], r["kind"], r["text"], r["expect"], "該当なし" if r["none"] else "表示",
                            r["hit"], r["doc_hit"], r["first"], r["conf"], round(r["cos"], 4), "" if r["fts"] is None else round(r["fts"], 4)])
        print(f"\nCSVを書きました：{csv_path}")


if __name__ == "__main__":
    main()
