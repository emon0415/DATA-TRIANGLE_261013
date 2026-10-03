# =============================================================
# tokenizer.py — 形態素解析（SudachiPy）で、検索に使う語を取り出す
#
# 使い道は3つ：
#   ・index_text(本文)   … 全文検索の列に入れる「空白区切りの語」を作る（文書を登録するとき）
#   ・query_text(質問文) … PGroonga に渡す検索の条件を作る（検索するとき）
#   ・keywords(本文)     … 画面の1段目に出す「文書のキーワード」を作る
#
# 語の取り出し方：
#   ・名詞（と、名詞につながる接頭辞・接尾辞）を残す。助詞・動詞・記号などは捨てる
#   ・連続する名詞は1つの複合語にまとめる（例：設備／保全／課 → 設備保全課）
#     辞書にない複合語も、ひとかたまりで一致させるため
#   ・複合語に加えて、その部分の語も残す（例：設備保全課 設備 保全）
#     「保全」で検索しても見つかるようにするため（AとCの併用と同じ考え方）
#   ・表記ゆれは正規化した形にそろえる（例：打合せ → 打ち合わせ）
#   ・型番や文書IDのような英数字とハイフンの並びは、1つの語として残す（例：xyz-9982）
#
# 必要なもの：pip install sudachipy sudachidict_core
# =============================================================
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from sudachipy import dictionary, tokenizer as sudachi_tokenizer

MODE_A = sudachi_tokenizer.Tokenizer.SplitMode.A
MODE_C = sudachi_tokenizer.Tokenizer.SplitMode.C

# 型番や文書IDのような並び（英数字をハイフンなどでつないだもの）
CODE_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)+")

# 1語で使われても意味の薄い名詞（必要に応じて足す）
STOPWORDS = {
    "こと", "もの", "ため", "よう", "これ", "それ", "あれ", "ここ", "そこ", "とき",
    "場合", "際", "等", "方", "点", "中", "上", "下", "前", "後", "他", "以上", "以下",
    "今回", "今後", "現在", "全体", "一部", "それぞれ", "ほか",
}


@dataclass
class Term:
    """取り出した語の1つ。compound は複合語そのものか、kind で区別する"""
    text: str      # 正規化した形
    kind: str      # "compound"（複合語）／"word"（単独の語）／"part"（複合語の部分）／"code"（型番など）


@lru_cache(maxsize=1)
def _tokenizer():
    """辞書の読み込みは重いので1回だけ行う"""
    d = dictionary.Dictionary(dict="core")
    return d.tokenizer() if hasattr(d, "tokenizer") else d.create()


def _is_noun_like(m):
    pos = m.part_of_speech()
    if pos[0] == "名詞":
        return pos[1] not in ("代名詞",)
    return pos[0] in ("接頭辞", "接尾辞")


def _norm(m):
    return m.normalized_form().lower()


def terms(text):
    """本文から、検索に使う語を順番どおりに取り出す（同じ語が何度出てもよい）"""
    out = []
    # 1) 型番などを先に取り出し、形態素解析の対象からは空白に置き換える
    for code in CODE_PATTERN.findall(text):
        out.append(Term(code.lower(), "code"))
    text = CODE_PATTERN.sub(" ", text)

    # 2) 名詞の並びをまとめる
    run = []
    def flush():
        nouns = [m for m in run if m.part_of_speech()[0] == "名詞"]
        if not nouns:
            run.clear(); return
        if len(run) == 1:
            w = _norm(run[0])
            if w not in STOPWORDS and run[0].part_of_speech()[1] != "数詞":
                out.append(Term(w, "word"))
        else:
            out.append(Term("".join(_norm(m) for m in run), "compound"))
            seen = set()
            for m in run:                       # 部分の語（Cの単位とAの単位）
                for p in [m] + list(m.split(MODE_A)):
                    w = _norm(p)
                    if len(w) >= 2 and w not in STOPWORDS and w not in seen \
                            and p.part_of_speech()[1] != "数詞":
                        seen.add(w); out.append(Term(w, "part"))
        run.clear()

    for m in _tokenizer().tokenize(text, MODE_C):
        if _is_noun_like(m):
            run.append(m)
        else:
            flush()
    flush()
    return out


def index_text(text):
    """全文検索の列に入れる文字列（空白区切り）。PGroonga はこの列に索引を張る"""
    return " ".join(t.text for t in terms(text))


def query_terms(text):
    """質問文から、検索に使う語を重複なしで取り出す"""
    return list(dict.fromkeys(t.text for t in terms(text)))


def query_text(text):
    """PGroonga の検索条件（&@~ に渡す）。どれか1語を含めば一致するよう OR でつなぐ"""
    def quote(w):
        return '"' + w.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return " OR ".join(quote(w) for w in query_terms(text))


def keywords(text, top_n=5):
    """画面に出すキーワード。複合語・単独の語・型番から、出現回数の多い順に選ぶ"""
    c = Counter(t.text for t in terms(text) if t.kind != "part" and len(t.text) >= 2)
    return [w for w, _ in c.most_common(top_n)]