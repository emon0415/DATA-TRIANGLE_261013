# =============================================================
# _test/profiles/test_outreach.py — 「相談メッセージの下書き」の検査（DBもOpenAIも使わない）
# 使い方（リポジトリ直下で実行）：python _test/profiles/test_outreach.py
# =============================================================
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from search import outreach as O   # noqa: E402


def check(cond, msg):
    print(("OK  " if cond else "NG  ") + msg)
    if not cond:
        sys.exit(1)


BRIEF = {"sections": {
    "得意分野": [{"text": "洗浄液の再生利用に取り組んだ。", "sources": ["PJ-2016-0001"]}],
    "意欲のテーマ": [{"text": "本人は、エネルギーの見える化を希望している。", "sources": ["キャリアシート：将来やりたいこと"]}],
    "実行の実績": [], "留意点": [],
    "今回の質問との関係": [{"text": "", "sources": ["PJ-2016-0001"], "level": "なし"}]}, "dropped": []}


class Fake:
    def __init__(self, text):
        self.text = text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.sent = None

    def create(self, **kw):
        self.sent = kw["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))])


lines = O.experience_lines(BRIEF)
check(len(lines) == 2 and "PJ-2016-0001" in lines[0], "出典つきの経験だけが材料になる（「なし」の関係は使わない）")
check(O.allowed_codes(BRIEF) == {"PJ-2016-0001"}, "使ってよい文書番号は、紹介文の出典にあるものだけ")

msgs = O.build_messages("山田 太郎", "第一工場 製造課", "洗浄液を減らしたい", BRIEF)
check("山田 太郎さん" in msgs[1]["content"] and "洗浄液を減らしたい" in msgs[1]["content"], "宛先と相談したいことが、モデルに渡る")
check("（まだ決まっていない" in O.build_messages("A", "B", "", BRIEF)[1]["content"], "相談したいことが空でも、書ける")

v = O.verify("PJ-2016-0001に関心があります。PJ-2099-0001も拝見しました。15分お時間をください。", BRIEF)
check("PJ-2099-0001" not in v["text"] and len(v["dropped"]) == 1 and "PJ-2016-0001" in v["text"], "材料にない文書番号を含む文は、捨てる")

f = Fake("はじめてご連絡します。PJ-2016-0001の取り組みに関心があります。")
out = O.generate(f, "山田 太郎", "第一工場 製造課", "洗浄液を減らしたい", BRIEF)
check(not out["empty"] and "PJ-2016-0001" in out["text"] and f.sent is not None, "下書きが返る")

EMPTY = {"sections": {"得意分野": [], "意欲のテーマ": [], "実行の実績": [], "留意点": [], "今回の質問との関係": []}}
f2 = Fake("使われないはず")
out2 = O.generate(f2, "山田 太郎", "第一工場 製造課", "x", EMPTY)
check(out2["empty"] and f2.sent is None, "材料がないときは、モデルを呼ばずに、書けないと返す")
print("\nすべて通りました。")
