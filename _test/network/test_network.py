# =============================================================
# _test/network/test_network.py — つながりマップの計算の検査（DBもOpenAIも使わない。人工の小さなデータで確かめる）
# 使い方（リポジトリ直下で実行）：python _test/network/test_network.py
# =============================================================
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from search import network as N   # noqa: E402


def check(cond, msg):
    print(("OK  " if cond else "NG  ") + msg)
    if not cond:
        sys.exit(1)


def unit(*v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


# 5人：A（中心）、B（共同作業・看板も近い）、C（同じ課・近い）、D（別の区分・看板だけ近い＝意外なつながり）、E（別の区分・どれも遠い）
people = ["A", "B", "C", "D", "E"]
dept = {"d1": {"type": "工場", "site": "第一工場", "func": "品質保証", "name": "第一工場 品質保証課"},
        "d2": {"type": "工場", "site": "第一工場", "func": "製造", "name": "第一工場 製造課"},
        "d3": {"type": "本社", "site": None, "func": "経営企画", "name": "経営企画部"}}
ctx = {"emps": {"A": {"dept_id": "d1"}, "B": {"dept_id": "d1"}, "C": {"dept_id": "d1"}, "D": {"dept_id": "d3"}, "E": {"dept_id": "d3"}}}
net = {
    "people": people, "index": {e: i for i, e in enumerate(people)}, "dept": dept, "vocab_k": ["段取り", "切り粉", "予防保全"],
    "mk": np.array([unit(1, 0, 0), unit(1, .1, 0), unit(1, .3, 0), unit(0, 0, 1), unit(0, 1, 0)], dtype=np.float32),   # 文書のキーワード
    "mt": np.array([unit(1, 0), unit(1, .1), unit(1, .3), unit(0, 1), unit(0, 1)], dtype=np.float32),                 # 文書の本文の語
    "mp": np.array([unit(1, 0, 0), unit(1, .2, 0), unit(1, .3, 0), unit(1, .05, 0), unit(0, 0, 1)], dtype=np.float32),  # 看板
    "has_p": np.array([True] * 5),
    "docs_of": {"A": {1, 2}, "B": {2}, "C": {3}, "D": {4}, "E": {5}}}

w = N.weights(0.5)
check(abs(w[3]) < 1e-9 and abs(sum(w[:3]) - 0.70) < 1e-9, "中央の重みは 看板0.35／テーマ0.20／文章0.15／知っている度0")
check(abs(N.weights(0)[3] - 0.30) < 1e-9 and abs(N.weights(1)[3] + 0.30) < 1e-9, "知っている度は、左端 +0.30、右端 −0.30")

r = N.neighbors(net, ctx, "A", t=0.0, known_range=2, top_n=4)
check(r["status"] == "ok" and r["people"][0]["emp"] in ("B", "C"), "左端（すでに強い関係）では、共同作業・同じ課の人が上に来る")
check(next(p for p in r["people"] if p["emp"] == "B")["level"] == 0, "同じ文書に関わった人は、段階が「共同作業」")

r = N.neighbors(net, ctx, "A", t=1.0, known_range=2, top_n=4)
d = next(p for p in r["people"] if p["emp"] == "D")
check(d["surprise"] and d["level"] == 5, "看板は近いのに、文書のテーマが遠い、別の区分の人は「意外なつながり」")
r_all = N.neighbors(net, ctx, "A", t=1.0, known_range=5, top_n=4)
check(not next(p for p in r_all if False) if False else not next(p for p in r_all["people"] if p["emp"] == "D")["surprise"],
      "「知っている範囲」の中の人は、意外なつながりにしない")
check(d["words"] == [], "意外なつながりの人には、共通する語が出ない（看板の語は出さない）")
check(all(len(p["parts"]) == 4 for p in r["people"]), "点の内訳は、看板・テーマ・文章・知っている度の4つ")

net2 = dict(net, has_p=np.array([False] + [True] * 4))
r = N.neighbors(net2, ctx, "A")
check(all(p["parts"][0] == 0 for p in r["people"]), "中心の人に看板がないときは、看板の点は0（文書だけで探す）")
rf = N.neighbors(net, ctx, "A", t=1.0, known_range=2, top_n=4, use_profile=False)["people"]
check(all(x["parts"][0] == 0 and not x["surprise"] for x in rf), "事実だけで描くときは、看板の点が0で、◆も出ない")
check(all(x["words"] is not None for x in rf) and len(rf) == 4, "事実だけで描いても、人は出る")
check(N.FACT["人を探す"]["t"] == 0.0 and N.FACT["属人化"]["range"] == 0, "人を探す・属人化マップの固定値")
check(N.neighbors(net, ctx, "Z")["status"] == "no_docs", "文書も看板もない人は、描けない")
print("\nすべて通りました。")
