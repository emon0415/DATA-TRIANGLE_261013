# =============================================================
# outreach.py — 「相談メッセージの下書き」：見つけた人に、最初の一言を送るための依頼文を、小型モデルに書かせる
#
# 「この人についてもっと調べる」（person_brief）を押したあとにだけ、ボタンを出す。材料は、その紹介文（出典つき）と、相談したいこと。
#
# 書かせる内容：あいさつ → 相手の経験のどこに関心を持ったか（出典の文書名に触れる）→ 相談したいこと → 小さな最初の依頼（15分ほど など）
# 決まり：
#   ・材料の紹介文に書かれた経験だけを、相手の経験として書く。作らない。褒め言葉・お世辞は書かない
#   ・文書番号は、材料にあるものだけ。材料にない番号を含む文は、プログラムで捨てる
#   ・差出人の名前は、画面ではまだ分からないので「（あなたの名前）」にして、本人が直す
#   ・看板（非公開メモ）の内容は、そのまま引用しない（紹介文に載ったものだけ使う。紹介文の中の、キャリアシート由来の文は、
#     「本人が書いている」という形のままなので、メッセージでは「…に関心がおありと伺い」のように、柔らかく触れるか、触れない）
# DB・OpenAIに触れる部分（generate）と、触れない部分（build_messages / verify）を分けてある。
# =============================================================
import re

MODEL = "gpt-4o-mini"
MAX_CHARS = 420                 # 本文の長さの目安（字）
CODE_RE = re.compile(r"[A-Z]{2,5}-\d{4}-\d{4}")

SYSTEM = """あなたは、社内で「経験のある人に、初めて相談する」メッセージの下書きを書く係です。
与えられた【材料】だけを根拠に、日本語で、%d字ほどの本文を書いてください。

書き方の決まり：
 1. 構成：（1）はじめてのご連絡であることと、簡単な自己紹介（名前は「（あなたの名前）」、所属は「（あなたの所属）」のまま）
          （2）相手の経験のどこに関心を持ったか。【相手の経験】に書かれたことだけを、文書名（出典の文書番号つき）に触れて、事実のまま書く
          （3）相談したいこと（【相談したいこと】を、そのまま短くまとめる）
          （4）小さな最初のお願い（15分ほどのオンライン相談、メールでの一言、など。負担の軽いもの）
          （5）お忙しければ見送ってよい、という一言
 2. 【相手の経験】にないこと（経験、能力、人柄、成果、年数）は、書かない。推測もしない
 3. 褒め言葉・お世辞（素晴らしい、ご活躍、さすが、など）は書かない。事実を、丁寧に、平らな言い方で書く
 4. 中止・不採択・保留などの状態は、書かなくてよい。書くときは、事実のまま
 5. 文書番号は、【相手の経験】に書かれたものだけを使う。作らない。「このメッセージは AI の下書き」とは書かない
 6. 本文だけを返す。件名・見出し・箇条書き・かぎ括弧の説明は付けない
""" % MAX_CHARS


def experience_lines(brief):
    """紹介文（person_brief.generate の結果）から、メッセージの材料にする行を作る。出典つきの文だけ。
    「今回の質問との関係」が「なし」の文は使わない"""
    out = []
    for sec in ("得意分野", "実行の実績", "意欲のテーマ", "今回の質問との関係"):
        for it in (brief.get("sections", {}).get(sec) or []):
            if not it.get("text") or not it.get("sources"):
                continue
            if sec == "今回の質問との関係" and it.get("level") == "なし":
                continue
            out.append(f"・（{sec}）{it['text']}　出典：{'、'.join(it['sources'])}")
    return out


def allowed_codes(brief):
    return {c for sec in brief.get("sections", {}).values() for it in sec for s in it.get("sources", []) for c in CODE_RE.findall(s)}


def build_messages(name, dept, query, brief):
    """モデルに渡すメッセージを作る。DB・OpenAIには触れない"""
    lines = experience_lines(brief)
    user = (f"【宛先】{name}さん（{dept}）\n\n【相手の経験】\n" + ("\n".join(lines) if lines else "（材料なし）")
            + f"\n\n【相談したいこと】\n{query.strip() if query and query.strip() else '（まだ決まっていない。相手の経験に関心があり、お話を聞きたい、という形にする）'}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def verify(text, brief):
    """書かれた本文を確かめる：材料にない文書番号を含む文は捨てる。戻り値：{"text", "dropped"}"""
    ok = allowed_codes(brief)
    kept, dropped = [], []
    for s in re.split(r"(?<=[。！？\n])", text.strip()):
        bad = [c for c in CODE_RE.findall(s) if c not in ok]
        (dropped if bad else kept).append(s)
    return {"text": "".join(kept).strip(), "dropped": [d.strip() for d in dropped if d.strip()]}


def generate(client, name, dept, query, brief, model=MODEL):
    """依頼文の下書きを書かせて、確かめた結果を返す。client は OpenAI（テストでは偽物）。
    材料がない（紹介文に出典つきの経験がない）ときは、書かずに {"text": "", "empty": True} を返す"""
    if not experience_lines(brief):
        return {"text": "", "dropped": [], "empty": True}
    res = client.chat.completions.create(model=model, messages=build_messages(name, dept, query, brief), temperature=0.4)
    out = verify(res.choices[0].message.content or "", brief)
    out["empty"] = not out["text"]
    out["model"] = model
    return out
