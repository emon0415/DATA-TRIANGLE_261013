# =============================================================
# _test/profiles/test_profiles.py — 看板を作る処理を、偽のDBで確かめる（DBもOpenAIも使わない）
# 使い方（リポジトリ直下で実行）：python _test/profiles/test_profiles.py
# 確かめること：件数、2回目の実行で何も書き換えないこと、要約・カルテのノートを壊さないこと、文書番号が混ざらないこと
# =============================================================
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from search import embed, profiles, profiles_store   # noqa: E402

DATA = json.loads((ROOT / "initial_load" / "data" / "db_load_673.json").read_text(encoding="utf-8"))


class Result:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class Query:
    def __init__(self, db, name):
        self.db, self.name, self.rows, self.keys, self.span = db, name, None, [], None

    def select(self, cols, count=None, head=False):
        self.rows = list(self.db.tables[self.name].values())
        return self

    def order(self, col):
        self.keys.append(col)
        return self

    def range(self, a, b):
        self.span = (a, b)
        return self

    def execute(self):
        rows = sorted(self.rows, key=lambda r: tuple(r[k] for k in self.keys)) if self.keys else self.rows
        if self.span:
            rows = rows[self.span[0]:self.span[1] + 1]
        return Result([dict(r) for r in rows])


class Upsert:
    def __init__(self, db, name, rows, conflict):
        self.db, self.name, self.rows, self.conflict = db, name, rows, conflict.split(",")

    def execute(self):
        table = self.db.tables[self.name]
        for r in self.rows:
            key = tuple(r[c] for c in self.conflict)
            if key in table:
                table[key].update(r)
            else:
                table[key] = dict(r)
                if self.name == "profile_notes":
                    table[key]["note_id"] = len(table)
        self.db.writes[self.name] += len(self.rows)
        return Result([])


class FakeDB:
    def __init__(self):
        doc_id = {d["doc_id"]: i for i, d in enumerate(DATA["documents"], 1)}
        emp_id = {e["emp_no"]: i for i, e in enumerate(DATA["employees"], 1)}
        self.writes = {"profile_notes": 0, "profiles": 0}
        self.tables = {
            "documents": {doc_id[d["doc_id"]]: dict(d, doc_id=doc_id[d["doc_id"]]) for d in DATA["documents"]},
            "document_sections": {(doc_id[s["doc_id"]], int(s["section_no"])): dict(s, doc_id=doc_id[s["doc_id"]], section_no=int(s["section_no"]))
                                  for s in DATA["document_sections"]},
            "document_authors": {(doc_id[a["doc_id"]], emp_id[a["emp_no"]]): {"doc_id": doc_id[a["doc_id"]], "emp_id": emp_id[a["emp_no"]], "role": a["role"]}
                                 for a in DATA["document_authors"]},
            "document_keywords": {(doc_id[k["doc_id"]], k["keyword"]): dict(k, doc_id=doc_id[k["doc_id"]]) for k in DATA["document_keywords"]},
            "profile_notes": {}, "profiles": {},
        }
        self.doc_id, self.emp_id = doc_id, emp_id

    def table(self, name):
        db = self

        class T:
            def select(self, *a, **k):
                return Query(db, name).select(*a, **k)

            def upsert(self, rows, on_conflict):
                return Upsert(db, name, rows, on_conflict)
        return T()


def check(cond, msg):
    print(("OK  " if cond else "NG  ") + msg)
    if not cond:
        sys.exit(1)


def main():
    embed.embed_texts = lambda texts: [[0.1, 0.2, 0.3] for _ in texts]   # OpenAIを呼ばない
    db = FakeDB()

    r = profiles_store.rebuild(db, dry_run=True)
    check(db.writes == {"profile_notes": 0, "profiles": 0}, "dry-run では何も書かない")
    people = len({a["emp_no"] for a in DATA["document_authors"]})
    check(r["people"] == people, f"看板を作る人が、著者の人数と同じ（{people}人）")

    r = profiles_store.rebuild(db)
    check(len(db.tables["profiles"]) == people, "全員の看板が書かれた")
    check(len(db.tables["profile_notes"]) == len(DATA["document_authors"]), f"ノートが、著者の行の数と同じ（{len(DATA['document_authors'])}枚）")
    p = next(iter(db.tables["profiles"].values()))
    check(p["model"] is None and p["embedded_at"] and 1 <= len(p["profile_text"]) <= 3000, "看板の文章は1〜3000字で、モデルは空、埋め込みの日時がある")
    check(all(1 <= len(n["body"]) <= 1000 and n["body_source"] == "抜粋" for n in db.tables["profile_notes"].values()), "ノートの本文は1〜1000字で、由来は抜粋")
    check(all("KZ-" not in x["profile_text"] and "IDEA-" not in x["profile_text"] for x in db.tables["profiles"].values()), "看板の文章に文書番号が混ざらない")

    before = dict(db.writes)
    r2 = profiles_store.rebuild(db)
    check(r2["profiles"] == 0 and r2["embedded"] == 0, "2回目は、看板を1人も書き換えず、埋め込みも作らない")
    check(db.writes["profiles"] == before["profiles"], "2回目に profiles を書かない")

    # 要約にしたノートと、カルテを置いて、作り直しても壊れないこと
    emp = next(iter(db.tables["profiles"]))[0]   # 偽DBは (社員ID,) をキーにしている
    key = next(k for k in db.tables["profile_notes"] if k[0] == emp)
    db.tables["profile_notes"][key].update(body="要約された本文", body_source="要約", model="gpt-4o-mini")
    db.tables["profile_notes"][(emp, None)] = {"note_id": 99999, "emp_id": emp, "doc_id": None, "body": "リーダー経験あり。アプリ開発が趣味",
                                               "body_source": "本人入力", "created_at": "2026-10-07T00:00:00+00:00"}
    profiles_store.rebuild(db)
    check(db.tables["profile_notes"][key]["body"] == "要約された本文", "要約のノートを、抜粋で上書きしない")
    text = db.tables["profiles"][(emp,)]["profile_text"]
    check("要約された本文" in text and "リーダー経験あり" in text, "看板の文章に、要約とカルテが入る")
    check(db.tables["profile_notes"][(emp, None)]["body"].startswith("リーダー"), "カルテのノートを書き換えない")

    # 登録のあとに、著者だけ作り直す
    n = db.writes["profile_notes"]
    profiles_store.rebuild(db, emp_ids={emp})
    check(db.writes["profile_notes"] - n <= 10, "emp_ids を渡すと、その人のノートだけ書く")
    print("\nすべて通りました。")


main()
