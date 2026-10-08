# =============================================================
# _test/profiles/test_blend.py — 看板の混ぜ込み（rank.rank_people の alpha）を、手元のJSONで確かめる
# 使い方（リポジトリ直下で実行）：python _test/profiles/test_blend.py   （DBもOpenAIも使わない）
# 確かめること：alpha=1 のとき、看板なしの順位と完全に同じ／alpha を下げると看板の近い人が上がる／
#             文書の経路に出ない人も、看板が近ければ候補に入る／絞り込み・本人除外が効く
# =============================================================
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from search import rank   # noqa: E402

D = json.loads((ROOT / "initial_load" / "data" / "db_load_673.json").read_text(encoding="utf-8"))


def check(cond, msg):
    print(("OK  " if cond else "NG  ") + msg)
    if not cond:
        sys.exit(1)


def main():
    secs = [{"section_id": i, "doc_id": s["doc_id"]} for i, s in enumerate(D["document_sections"], 1)]
    emps = [{"emp_id": e["emp_no"], "dept_id": e["dept_id"], "is_active": e.get("is_active", True)} for e in D["employees"]]
    docs = [{k: d.get(k) for k in ("doc_id", "doc_type", "result", "pj_status", "submitted_at", "started_at")} for d in D["documents"]]
    auth = [{"doc_id": a["doc_id"], "emp_id": a["emp_no"], "role": a["role"]} for a in D["document_authors"]]
    corpus = rank.Corpus.from_rows(secs, docs, auth, emps)
    rnd = random.Random(1)
    ids = [s["section_id"] for s in secs]
    scores = {i: rnd.random() / 30 for i in rnd.sample(ids, 150)}

    base = rank.rank_people(scores, corpus, top_n=10)
    sims = {e["emp_id"]: rnd.random() * 0.2 + 0.2 for e in emps}
    same = rank.rank_people(scores, corpus, top_n=10, profile_sims=sims, alpha=1.0)
    key = lambda r: [(p["emp"], round(p["score"], 9)) for f in ("proven", "hidden") for p in r[f]]
    check(key(base) == key(same), "alpha=1 のとき、看板なしの順位と完全に同じ（点数も同じ）")
    check(key(base) == key(rank.rank_people(scores, corpus, top_n=10, profile_sims=None, alpha=0.5)), "看板の類似度を渡さなければ、alpha があっても何も変わらない")

    # 文書の経路に出ない人（章が当たっていない人）を選んで、看板だけ極端に近くする
    hit_emps = {p["emp"] for f in ("proven", "hidden") for p in rank.rank_people(scores, corpus, top_n=10 ** 6)[f]}
    outsider = next(e["emp_id"] for e in emps if e["emp_id"] not in hit_emps and e["is_active"])
    sims2 = dict(sims, **{outsider: 0.9})
    r = rank.rank_people(scores, corpus, top_n=3, profile_sims=sims2, alpha=0.5)
    top = [p["emp"] for f in ("proven", "hidden") for p in r[f]]
    check(outsider in top, "経路に出ない人でも、看板が飛びぬけて近ければ、上位に入る")
    r0 = rank.rank_people(scores, corpus, top_n=3, profile_sims=sims2, alpha=1.0)
    check(outsider not in [p["emp"] for f in ("proven", "hidden") for p in r0[f]], "alpha=1 なら、その人は入らない")

    # alpha を下げるほど、看板の点数の影響が大きくなる
    def prof_rank(alpha):
        res = rank.rank_people(scores, corpus, top_n=10 ** 6, profile_sims=sims2, alpha=alpha)
        allp = sorted(res["proven"] + res["hidden"], key=lambda p: -p["score"])
        return [p["emp"] for p in allp].index(outsider) + 1
    check(prof_rank(0.2) < prof_rank(0.8), "alpha を下げるほど、看板が近い人の順位が上がる")

    sc = [p["score"] for f in ("proven", "hidden") for p in rank.rank_people(scores, corpus, top_n=10 ** 6, profile_sims=sims2, alpha=0.5)[f]]
    check(max(sc) <= 1.0 + 1e-9 and min(sc) >= 0, "混ぜたあとの点数は、0〜1に収まる")

    # 絞り込み・本人除外
    dept = next(e["dept_id"] for e in emps if e["emp_id"] == outsider)
    r = rank.rank_people(scores, corpus, top_n=10, profile_sims=sims2, alpha=0.5, depts={"__none__"})
    check(not r["proven"] and not r["hidden"], "部署で絞ると、その部署以外は出ない")
    r = rank.rank_people(scores, corpus, top_n=10, profile_sims=sims2, alpha=0.5, searcher=outsider)
    check(outsider not in [p["emp"] for f in ("proven", "hidden") for p in r[f]], "検索した本人は、結果から除く")
    r = rank.rank_people(scores, corpus, top_n=10, profile_sims=sims2, alpha=0.5, years={2099})
    check(outsider not in [p["emp"] for f in ("proven", "hidden") for p in r[f]], "年で絞るときは、文書のない人（看板だけの人）は入れない")
    print("\nすべて通りました。")


main()
