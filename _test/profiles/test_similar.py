# =============================================================
# _test/profiles/test_similar.py — 仲間探し（search/similar.py）の中身を、手作りのベクトルで確かめる
# 使い方（リポジトリ直下で実行）：python _test/profiles/test_similar.py   （DBもOpenAIも使わない）
# =============================================================
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from search import similar as S   # noqa: E402


def check(cond, msg):
    print(("OK  " if cond else "NG  ") + msg)
    if not cond:
        sys.exit(1)


def unit(*v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def main():
    d0 = S.DEFAULTS
    check(d0["same_dept"] == 0.3 and S.SAME_DEPT_STEPS == [1.0, 0.6, 0.3], "同じ部署の係数：選択肢は1.0／0.6／0.3、既定は0.3")
    ids = [1, 2, 3, 4, 5]
    m = np.array([unit(1, 0, 0), unit(1, .1, 0), unit(1, .5, 0), unit(0, 1, 0), unit(1, .2, 0)])
    emps = {1: {"dept_id": 10}, 2: {"dept_id": 10}, 3: {"dept_id": 20}, 4: {"dept_id": 20}, 5: {"dept_id": 30, "is_active": False}}
    docs = {1: [(100, "主担当"), (101, "提案者")], 2: [(100, "副担当"), (102, "提案者")], 3: [(200, "提案者")]}

    r = S.find_similar(1, ids, m, emps, docs, same_dept=1.0, min_sim=0.5, top_n=5)
    check([p["emp"] for p in r["people"]] == [2, 3], "近い順に並ぶ。本人・退職者・足切り未満（4）は出ない")
    check(r["candidates"] == 3 and r["passed"] == 2, "候補3人のうち、足切りを通ったのは2人")

    r = S.find_similar(1, ids, m, emps, docs, same_dept=1.0, min_sim=0.5, top_n=1)
    check(len(r["people"]) == 1 and r["passed"] == 2, "人数の上限で切る（通った人数は変わらない）")

    r = S.find_similar(1, ids, m, emps, docs, same_dept=1.0, min_sim=0.999)
    check(r["status"] == "none" and r["people"] == [], "全員が足切り未満なら「おすすめなし」")

    r = S.find_similar(1, ids, m, emps, docs, min_sim=0.1, same_dept=0.2)
    check([p["emp"] for p in r["people"]] == [3, 2], "同じ部署の係数×0.2で、別部署の人が上に来る")
    check(abs(r["people"][1]["score"] - r["people"][1]["sim"] * 0.2) < 1e-6, "同じ部署の人の点＝近さ×0.2")

    r = S.find_similar(1, ids, m, emps, docs, min_sim=0.5, same_dept=0.2)
    check([p["emp"] for p in r["people"]] == [3], "足切りは調整後の点に掛かるので、×0.2の人は消える")

    r = S.find_similar(1, ids, m, emps, docs, same_dept=1.0, min_sim=0.5, overlap_boost=1.0)
    p2 = next(p for p in r["people"] if p["emp"] == 2)
    p3 = next(p for p in r["people"] if p["emp"] == 3)
    check(abs(p2["overlap"] - 1 / 3) < 1e-9 and p3["overlap"] == 0, "重なり：1/3（和集合3件のうち共通は100だけ）と0")
    check(p2["shared"] == 1 and p3["shared"] == 0, "共通の文書の件数：1件と0件")
    check(abs(p3["score"] - p3["sim"] * 2) < 1e-6, "重なりが0の人は、加点の分だけ点が上がる（1＋1.0×1）")
    check(abs(p2["score"] - p2["sim"] * (1 + 2 / 3)) < 1e-6, "重なりのある人の加点は小さい")

    check(S.find_similar(99, ids, m, emps, docs)["status"] == "no_profile", "看板のない人は no_profile")
    check(S.overlap([], []) == 0.0, "どちらも文書がなければ重なり0")
    d = S.distribution(ids, m)
    check(set(d) == {50, 90, 95, 99, 99.9} and d[99] >= d[50], "分布のパーセンタイル")
    print("\n全部OK")


if __name__ == "__main__":
    main()
