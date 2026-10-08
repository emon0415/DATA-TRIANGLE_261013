# =============================================================
# _test/profiles/test_person_brief.py — 「この人についてもっと調べる」の検査（DBもOpenAIも使わない）
# 使い方（リポジトリ直下で実行）：python _test/profiles/test_person_brief.py
# =============================================================
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from search import person_brief as B   # noqa: E402


def check(cond, msg):
    print(("OK  " if cond else "NG  ") + msg)
    if not cond:
        sys.exit(1)


ROWS = [
    {"doc_id": 1, "body": "洗浄液を再生して使う。", "body_source": "抜粋", "memo_kind": None, "updated_at": "x",
     "documents": {"doc_code": "PJ-2016-0001", "title": "洗浄液の再生利用", "doc_type": "project", "pj_status": "完了", "started_at": "2016-09-05"}},
    {"doc_id": 2, "body": "排熱を回収する。7年超で中止。", "body_source": "抜粋", "memo_kind": None, "updated_at": "x",
     "documents": {"doc_code": "PJ-2024-0045", "title": "乾燥炉の排熱回収", "doc_type": "project", "pj_status": "中止", "started_at": "2024-09-02"}},
    {"doc_id": 3, "body": "点検の記録を揃えたい。", "body_source": "抜粋", "memo_kind": None, "updated_at": "x",
     "documents": {"doc_code": "KZ-2025-0012", "title": "点検記録の統一", "doc_type": "proposal", "result": "不採択", "submitted_at": "2025-04-01"}},
    {"doc_id": None, "body": "工場全体のエネルギーを見える化したい。", "body_source": "本人入力", "memo_kind": "将来やりたいこと",
     "updated_at": "2026-08-27T00:00:00", "documents": None},
]
NOTES = B.notes_from_rows(ROWS, {1: "責任者", 2: "責任者", 3: "提案者"})


def fake_client(payload):
    msgs = {}
    def create(**kw):
        msgs.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))],
                               usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=300))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), msgs


def main():
    by = {n["source"]: n for n in NOTES}
    check(set(by) == {"PJ-2016-0001", "PJ-2024-0045", "KZ-2025-0012", "キャリアシート：将来やりたいこと"}, "出典は、文書番号とキャリアシートの項目名")
    check(by["PJ-2016-0001"]["role"] == "責任者" and by["KZ-2025-0012"]["status"] == "不採択", "役割・状態が材料に入る")
    check([n["date"] for n in NOTES] == sorted(n["date"] for n in NOTES), "日付順に並ぶ")

    payload = {
        "得意分野": [{"text": "洗浄液の再生利用をPJで責任者として進めた。", "sources": ["PJ-2016-0001"]}],
        "意欲のテーマ": [{"text": "本人は、工場全体のエネルギーの見える化を希望している。", "sources": ["キャリアシート：将来やりたいこと"]},
                   {"text": "点検記録の統一を提案した（不採択）。", "sources": ["KZ-2025-0012", "KZ-9999-9999"]}],
        "実行の実績": [{"text": "排熱回収を進めた。", "sources": ["PJ-2024-0045"]},
                   {"text": "洗浄液の購入量を減らした。", "sources": ["PJ-2016-0001"]},
                   {"text": "架空の実績。", "sources": ["PJ-0000-0000"]},
                   {"text": "出典のない主張。", "sources": []}],
        "今回の質問との関係": [{"text": "省エネの経験が近い。", "sources": ["PJ-2016-0001"]}],
        "留意点": [{"text": "乾燥炉の排熱回収は、投資回収が条件に合わず中止。", "sources": ["PJ-2024-0045"]}],
    }
    client, sent = fake_client(payload)
    out = B.generate(client, NOTES, query="省エネの経験者", full_texts={1: "■目的\n洗浄液…"})
    s = out["sections"]
    check(sent["response_format"] == {"type": "json_object"} and sent["model"] == B.MODEL, "JSONで、決めたモデルを呼ぶ")
    user = sent["messages"][1]["content"]
    check("【出典】PJ-2016-0001" in user and "（上の文書の章の全文）" in user and "省エネの経験者" in user, "材料に出典・全文・質問文が入る")
    check("エネルギーを見える化したい" in user and "本人の申告" in user, "キャリアシートは本人の申告として渡す")
    check(s["意欲のテーマ"][1]["sources"] == ["KZ-2025-0012"], "材料にない出典（KZ-9999-9999）は外す")
    texts = [i["text"] for i in s["実行の実績"]]
    check(texts == ["洗浄液の購入量を減らした。"], "実績には、中止PJ・架空の出典・出典なしを入れない")
    check(s["留意点"][0]["sources"] == ["PJ-2024-0045"], "中止PJは、留意点には出典にできる")
    check(len(out["dropped"]) >= 3, "捨てたものが記録される")
    check(out["tokens"] == {"in": 1000, "out": 300}, "トークン数が戻る")

    out = B.verify(payload, NOTES, has_query=False)
    check(out["sections"]["今回の質問との関係"] == [], "質問文がなければ、質問との関係は空")
    out = B.verify("これはJSONではない", NOTES)
    check(all(v == [] for v in out["sections"].values()) and out["dropped"], "JSONでない返答でも止まらない")
    many = {"得意分野": [{"text": "あ" * 400, "sources": ["PJ-2016-0001"]}] * 5}
    out = B.verify(many, NOTES)
    check(len(out["sections"]["得意分野"]) == B.MAX_ITEMS and len(out["sections"]["得意分野"][0]["text"]) <= B.MAX_TEXT, "件数と長さの上限を守る")
    md = B.to_markdown(B.verify(payload, NOTES))
    check("**得意分野**" in md and "`PJ-2016-0001`" in md, "画面用の文章に出典が付く")
    print("\nすべて通りました。")


main()
