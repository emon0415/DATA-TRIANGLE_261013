# =============================================================
# _test/network/eval_network.py — 本物のDBで、つながりの強さの重みを確かめる（係数の調整用）
#
# 使い方（リポジトリ直下で実行。.env に SUPABASE_URL、SUPABASE_KEY がある）：
#   python _test/network/eval_network.py 2439 3849 1784        社員番号を1つ以上。左端・中央・右端の3つの設定で、上位10人を出す
#   python _test/network/eval_network.py 2439 --range 3        知っている範囲を変える（1＝同じ課まで／2＝拠点／3＝職能／4＝区分。既定2）
#   python _test/network/eval_network.py 2439 --n 15           表示人数
#
# 見るところ：
#   ・看板の近さ（相対）が、他の項目に比べて効きすぎていないか（内訳の「看板」が、いつも最大になっていないか）
#   ・右端で、別の部署・別の区分の人が上に来るか（来なければ、知っている度の重みを強める）
#   ・意外なつながり（◆）が、1人あたり0〜3人ほど出るか（多すぎれば、基準 SURPRISE を厳しくする）
#   ・看板の中身は出さない。共通する語は、文書のキーワードだけ
# =============================================================
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from register import db   # noqa: E402
from search import network as N, service, similar   # noqa: E402


def main(argv):
    nos = [a for a in argv if a.isdigit() and not argv[argv.index(a) - 1].startswith("--")]
    rng = int(argv[argv.index("--range") + 1]) if "--range" in argv else 2
    top_n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 10
    if not nos:
        sys.exit(__doc__)
    sb = db.connect()
    ctx = service.load(sb)
    net = N.load(sb, ctx, profile=similar.load_vectors(sb))
    print(f"対象の人 {len(net['people'])}人（看板あり {int(net['has_p'].sum())}人／文書あり {sum(1 for e in net['people'] if net['docs_of'][e])}人）")
    for no in nos:
        emp = next((e for e, v in ctx["emps"].items() if v["emp_no"] == no), None)
        if emp is None:
            print(f"\n社員番号 {no}：見つかりません")
            continue
        name, _, dept = service.person_line(ctx, emp)
        print(f"\n=== {name}（{dept}）社員番号 {no}")
        for label, t in (("左端（すでに強い関係）", 0.0), ("中央", 0.5), ("右端（新しい出会い）", 1.0)):
            res = N.neighbors(net, ctx, emp, t=t, known_range=rng, top_n=top_n)
            if res["status"] != "ok":
                print("  文書も看板もないため、描けません")
                break
            lv = Counter(N.LEVEL_LABELS[p["level"]] for p in res["people"])
            print(f"\n[{label}] 重み 看板{N.weights(t)[0]:.2f}／テーマ{N.weights(t)[1]:.2f}／文章{N.weights(t)[2]:.2f}／知っている度{N.weights(t)[3]:+.2f}"
                  f"　段階 {dict(lv)}　意外なつながり {sum(p['surprise'] for p in res['people'])}人")
            for p in res["people"]:
                n2, no2, d2 = service.person_line(ctx, p["emp"])
                a, b, c, d = p["parts"]
                print(f"  {p['score']:.2f}  {n2:<8}{d2:<16}{N.LEVEL_LABELS[p['level']]:<6}{'◆' if p['surprise'] else ' '} "
                      f"看板{a:.2f} テーマ{b:.2f} 文章{c:.2f} 知{d + 0.0:+.2f}  共通語:{','.join(p['words']) or '（なし）'}")


if __name__ == "__main__":
    main(sys.argv[1:])
