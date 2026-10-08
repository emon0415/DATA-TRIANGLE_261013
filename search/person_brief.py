# =============================================================
# person_brief.py — 「この人についてもっと調べる」：看板のノートから、その人の紹介を小型モデルに書かせる
#
# 検索の順位づけには看板を使わない。検索のあと、人を1人選んで「もっと調べる」を押したときだけ呼ぶ。
#
# 材料：その人のノート（文書由来の意欲・実行力と、キャリアシート）。質問文があるときは、近いノートの章の全文も足す。
# 書かせる項目：得意分野／意欲のテーマ／実行の実績／今回の質問との関係／留意点
# 決まり：
#   ・書いた事実には、必ず出典（文書番号、またはキャリアシートの項目名）を添えさせる
#   ・材料にない経験・人柄・能力は書かせない。キャリアシートは本人の申告なので「本人は…と書いている」と書かせる
#   ・返ってきた出典は、材料にあるものか、プログラムで確かめる。確かめられない項目は捨てる
#   ・「実行の実績」には、完了・進行中のPJだけを出典にできる（中止のPJは「留意点」にだけ書ける）
#
# DB・OpenAIに触れる部分（gather / fetch_sections / generate）と、触れない部分（build_messages / verify / to_markdown）を分けてある。
# 触れない部分は、_test/profiles/test_person_brief.py で試せる。
# =============================================================
import json
import re
from collections import defaultdict

from search import profiles as P

MODEL = "gpt-4o-mini"           # 小型モデル。1人あたり1円未満の見込み（入力 数千字、出力 千字ほど）
SECTIONS = ["得意分野", "意欲のテーマ", "実行の実績", "今回の質問との関係", "留意点"]
MAX_ITEMS = 3                   # 1項目に並べる文の数の上限
MAX_TEXT = 160                  # 1文の長さの上限（字）
FULL_TEXT_DOCS = 3              # 質問文に近い文書の、章の全文を足す件数の上限
FULL_TEXT_CHARS = 1200          # 足す全文の、1文書あたりの長さの上限（字）
CAREER = "キャリアシート"
NO_MATCH = "質問文に直接当たる経験は、材料にはない（最も近いノートを出典に示す）。"


# ---------- 材料を集める（DBを読む） ----------
def notes_from_rows(rows, roles):
    """profile_notes の行（documents を結合済み）から、材料のノートを作る。DBには触れない。
    rows: [{doc_id, body, body_source, memo_kind, updated_at, documents: {...}}]
    roles: {doc_id: その人の役割}"""
    notes = []
    for r in rows:
        d = r.get("documents") or {}
        if r.get("doc_id"):
            notes.append({
                "source": d.get("doc_code") or f"doc{r['doc_id']}", "doc_id": r["doc_id"],
                "kind": P.KIND.get(d.get("doc_type"), "意欲"), "doc_type": d.get("doc_type"),
                "label": P.doc_label(d) if d.get("doc_type") else "", "status": d.get("result") or d.get("pj_status") or "",
                "role": roles.get(r["doc_id"], ""), "date": P.doc_date(d), "title": d.get("title", ""), "body": r["body"]})
        else:
            notes.append({
                "source": f"{CAREER}：{r.get('memo_kind') or '本人入力'}", "doc_id": None,
                "kind": CAREER, "doc_type": None, "label": r.get("memo_kind") or "", "status": "",
                "role": "", "date": str(r.get("updated_at") or "")[:10], "title": "", "body": r["body"]})
    return sorted(notes, key=lambda n: (n["date"], n["source"]))


def gather(sb, emp_id):
    """その人のノートを、DBから読む"""
    rows = sb.table("profile_notes").select(
        "doc_id,body,body_source,memo_kind,updated_at,documents(doc_code,title,doc_type,result,pj_status,submitted_at,started_at)"
    ).eq("emp_id", emp_id).execute().data
    auth = sb.table("document_authors").select("doc_id,role").eq("emp_id", emp_id).execute().data
    return notes_from_rows(rows, {a["doc_id"]: a["role"] for a in auth})


def fetch_sections(sb, doc_ids):
    """文書の章の全文。{doc_id: 章を並べた文章}"""
    if not doc_ids:
        return {}
    rows = sb.table("document_sections").select("doc_id,section_no,section_name,body").in_("doc_id", list(doc_ids)).execute().data
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["doc_id"], r["section_no"])):
        by[r["doc_id"]].append(f"■{r['section_name']}\n{r['body']}")
    return {d: P.clip("\n".join(parts), FULL_TEXT_CHARS) for d, parts in by.items()}


# ---------- 書かせる（OpenAI） ----------
SYSTEM = """あなたは、社内の「人を探す」アプリで、ある人の紹介文の材料を整理する係です。
与えられた【材料】だけを根拠に、次の5項目をJSONで書いてください。

項目（キー名はこのとおり）：
  得意分野       … 文書（提案・PJ）に表れた経験から言える、分野・テーマ。キャリアシートだけを根拠にしない
  意欲のテーマ   … 提案・アイデア・キャリアシートに表れている、関心や取り組みたいこと
  実行の実績     … PJで実際にやったこと・成果（完了・進行中のPJだけ）。材料に書かれた事実と数字だけ
  今回の質問との関係 … 質問文があるときだけ。下の決まり6に従う
  留意点         … 事実だけ。次のものに限る：中止・進行中・不採択・保留などの状態、材料が少ないこと

書き方の決まり：
 1. 各項目は {"text": "1文（%d字以内）", "sources": ["出典", ...]} のリスト（最大%d件）。書けない項目は空のリストにする
 2. 出典は、材料に書かれた【出典】の文字列を、そのまま使う。作らない
 3. 材料に書かれていないこと（経験、能力、人柄、年数、成果の数字、理由、原因）は、書かない。推測もしない。数字は材料にあるものだけ
 4. キャリアシートは本人の申告。「本人は…と書いている」「…を希望している」と書き、「得意である」「成功させている」など、事実や評価として書かない
 5. 提案が不採択・保留・審査中でも、「意欲」の根拠にはなる。ただし、採択されたとは書かない。中止したPJや計画中のPJは、実績に書かない。留意点に、状態だけを書く
 6. 質問文との関係：項目に "level" を足し、次のどれか1つを選ぶ。
      "直接"   … 質問文の対象（何を、どうしたいか）と、同じ対象・同じ手段の経験がある。text にその経験を書く
      "一部"   … 同じ分野だが、対象か手段が違う。text に、何が同じで、何が違うかを書く
      "なし"   … 同じ工場・同じ設備分野・似た言葉があるだけで、対象も手段も違う。text は空でよい。sources に最も近いノートを1つ入れる
 7. 名前や性別は書かない。主語の「この人は」は付けない
 8. 評価や褒め言葉（優秀、実行力を発揮、成功、など）は書かない。事実を、短く、平らな言い方で書く
""" % (MAX_TEXT, MAX_ITEMS)


def build_messages(notes, query=None, full_texts=None):
    """モデルに渡すメッセージを作る。DB・OpenAIには触れない"""
    full_texts = full_texts or {}
    lines = ["【材料】"]
    for n in notes:
        head = f"【出典】{n['source']}"
        if n["kind"] == CAREER:
            lines.append(f"{head}\n種類：キャリアシート（本人の申告）｜項目：{n['label']}｜更新：{n['date']}\n{n['body']}")
            continue
        meta = f"種類：{n['kind']}｜{n['label']}｜この人の役割：{n['role']}｜日付：{n['date']}"
        lines.append(f"{head}\n{meta}\n表題：{n['title']}\n{n['body']}")
        if n["doc_id"] in full_texts:
            lines.append(f"（上の文書の章の全文）\n{full_texts[n['doc_id']]}")
    if query:
        lines.append(f"\n【質問文】\n{query}")
    else:
        lines.append("\n【質問文】なし（「今回の質問との関係」は空のリストにする）")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(lines)}]


def generate(client, notes, query=None, full_texts=None, model=MODEL, judge_model=None, check=True):
    """紹介を書かせて、確かめた結果を返す。client は OpenAI（テストでは偽物）
    check=True のとき、書いた文が出典の本文に本当に書かれているかを、別の呼び出しで確かめる（judge）"""
    res = client.chat.completions.create(
        model=model, messages=build_messages(notes, query, full_texts),
        response_format={"type": "json_object"}, temperature=0.2)
    raw = res.choices[0].message.content
    usage = getattr(res, "usage", None)
    out = verify(raw, notes, has_query=bool(query), full_texts=full_texts)
    tin, tout = getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
    if check:
        tin2, tout2 = judge(client, out, notes, full_texts, judge_model or model)
        tin, tout = tin + tin2, tout + tout2
    out["model"] = model
    out["tokens"] = {"in": tin, "out": tout}
    return out


JUDGE_SYSTEM = """あなたは、紹介文の検査係です。「文」と「出典の本文」を渡します。
文に書かれた主張が、出典の本文に書かれているかを、文ごとに判定してください。

判定（verdict）：
  "支持"   … 文の中のすべての主張（主語、数字、原因・理由、評価）が、出典の本文に書かれている
  "一部"   … 一部は書かれているが、書かれていない主張も混ざっている
  "不支持" … 書かれていない主張が中心にある

厳しく判定すること：
 ・もっともらしくても、本文に書かれていなければ「不支持」。推測、一般論、言い換えすぎ（本文にない関心・意図・理由）は「不支持」
 ・「〜のため」「〜なので」の理由や、「関連する」「関係する」のつながりは、本文にそのつながりが書かれていなければ「不支持」
 ・「精通」「得意」「成功」「優れた」などの評価は、本文に同じ評価が書かれていなければ「不支持」（事実として「目標を達成」と書かれていれば、それは「支持」）
 ・出典がキャリアシートのときは、本人が書いた範囲まで。「本人は…と書いている」は「支持」になるが、事実として言い切った文は「不支持」

JSONで返す：{"results": [{"i": 文の番号, "verdict": "支持|一部|不支持", "reason": "20字以内"}]}
"""


def judge(client, brief, notes, full_texts=None, model=MODEL):
    """brief の文を、出典の本文と突き合わせる。「不支持」の文は捨て、「一部」の文には注記する（brief を直接書き換える）。
    出典のない文（留意点など）と、決まった文（NO_MATCH）は、確かめない。確かめられなかったときは、文を残して、その旨を記録する。
    戻り値：(入力トークン, 出力トークン)"""
    by = {n["source"]: n for n in notes}
    flat = [(sec, it) for sec in SECTIONS for it in brief["sections"][sec] if it["sources"] and it["text"] != NO_MATCH]
    if not flat:
        return 0, 0
    blocks = []
    for i, (sec, it) in enumerate(flat):
        mats = []
        for src in it["sources"]:
            n = by[src]
            mats.append(f"［{src}］{n['body']}" + (f"\n{full_texts[n['doc_id']]}" if full_texts and n["doc_id"] in full_texts else ""))
        blocks.append(f"## {i}\n文：{it['text']}\n出典の本文：\n" + "\n".join(mats))
    try:
        res = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": "\n\n".join(blocks)}],
            response_format={"type": "json_object"}, temperature=0)
        verdicts = {int(r["i"]): r for r in json.loads(res.choices[0].message.content)["results"]}
    except Exception as e:                     # 確かめられなかったとき：文は残し、記録する
        brief["dropped"].append(f"文と出典の突き合わせができませんでした（{type(e).__name__}）。出典の有無しか確かめていません")
        return 0, 0
    drop = set()
    for i, (sec, it) in enumerate(flat):
        v = verdicts.get(i, {})
        if v.get("verdict") == "不支持":
            drop.add(id(it))
            brief["dropped"].append(f"［{sec}］出典の本文に書かれていないため捨てました（{v.get('reason', '')}）：{it['text'][:30]}…")
        elif v.get("verdict") == "一部":
            it["text"] += "（※出典に一部しか書かれていない）"
    for sec in SECTIONS:
        brief["sections"][sec] = [it for it in brief["sections"][sec] if id(it) not in drop]
    u = getattr(res, "usage", None)
    return getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0


# ---------- 確かめる（DB・OpenAIに触れない） ----------
def _digits(text):
    import unicodedata
    return set(re.findall(r"\d+(?:\.\d+)?", unicodedata.normalize("NFKC", str(text))))


def verify(raw, notes, has_query=True, full_texts=None):
    """返ってきたJSONを、材料と突き合わせる。
    戻り値：{"sections": {項目: [{"text", "sources"}]}, "dropped": [捨てた項目と理由]}
    ・出典が材料にないものは、出典から外す。出典が1つも残らない項目は捨てる（「留意点」は、出典なしでも残す）
    ・「実行の実績」は、完了・進行中のPJだけを出典にできる
    ・「得意分野」は、文書の出典を1つは含む（キャリアシートだけでは書けない）
    ・文の中の数字は、材料（ノート・章の全文）に書かれたものだけ。ないものは、その文を捨てる
    ・文の数と長さの上限を守らせる"""
    dropped = []
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return {"sections": {s: [] for s in SECTIONS}, "dropped": ["モデルの返答がJSONではありませんでした"]}
    by_source = {n["source"]: n for n in notes}
    material = _digits(" ".join([n["body"] + n["title"] + n["date"] for n in notes] + list((full_texts or {}).values())))

    def ok_for(section, n):
        if section != "実行の実績":
            return True
        return n["doc_type"] == "project" and n["status"] in ("完了", "進行中")

    sections = {}
    for sec in SECTIONS:
        items = data.get(sec) or []
        kept = []
        for it in items if isinstance(items, list) else []:
            text = str(it.get("text", "")).strip() if isinstance(it, dict) else ""
            if not text and not (sec == "今回の質問との関係" and isinstance(it, dict) and it.get("level") == "なし"):
                continue
            srcs = [s for s in (it.get("sources") or []) if isinstance(s, str)]
            good = [s for s in dict.fromkeys(srcs) if s in by_source and ok_for(sec, by_source[s])]
            if sec == "得意分野" and not any(by_source[s]["doc_id"] for s in good):
                dropped.append(f"［{sec}］文書の出典がない（キャリアシートだけ）ため捨てました：{text[:30]}…")
                continue
            extra = sorted(_digits(text) - material)
            if extra:
                dropped.append(f"［{sec}］材料にない数字 {extra} があるため捨てました：{text[:30]}…")
                continue
            bad = [s for s in srcs if s not in good]
            if bad:
                dropped.append(f"［{sec}］出典 {bad} は使えません：{text[:30]}…")
            if not good and sec != "留意点" and not (sec == "今回の質問との関係" and it.get("level") == "なし"):
                dropped.append(f"［{sec}］出典がないため捨てました：{text[:30]}…")
                continue
            text = re.sub(r"^この人(は|が)[、,]?", "", text)
            if sec == "今回の質問との関係":
                level = it.get("level")
                if level == "なし":
                    text = NO_MATCH
                elif level == "直接":
                    pass
                else:        # 「一部」と、決まりを守らなかったもの：直接ではないことを明示する
                    text = "直接ではない：" + text
            kept.append({"text": P.clip(text, MAX_TEXT), "sources": good})
        if not has_query and sec == "今回の質問との関係":
            kept = []
        sections[sec] = kept[:MAX_ITEMS]
    return {"sections": sections, "dropped": dropped}


def to_markdown(brief):
    """画面に出す文章（出典は文書番号のまま添える）"""
    out = []
    for sec in SECTIONS:
        items = brief["sections"].get(sec) or []
        if not items:
            continue
        out.append(f"**{sec}**")
        for it in items:
            src = "　`" + "` `".join(it["sources"]) + "`" if it["sources"] else ""
            out.append(f"- {it['text']}{src}")
        out.append("")
    return "\n".join(out)
