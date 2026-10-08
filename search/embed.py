# =============================================================
# embed.py — 質問文をベクトルにする（OpenAI）
#
# 章のベクトルは、登録のとき（register/embed.py）と初期ロード（initial_load/embed_sections.py）で
# DB に入れてある。検索のときは、質問文を「同じモデル」でベクトルにして比べる
#   モデル名は register/embed.py の MODEL をそのまま使い、食い違いが起きないようにする
#
# 接続情報：data-triangle フォルダ直下の .env の OPENAI_API_KEY
# 必要なもの：pip install openai numpy python-dotenv
# =============================================================
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from register.embed import MODEL   # 章のベクトルと同じモデル（text-embedding-3-small）

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _client():
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(".env に OPENAI_API_KEY が見つかりません。")
    return OpenAI()


def normalize(v):
    """ベクトルの長さを1にそろえる（内積がそのままコサイン類似度になる）"""
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n == 0, 1, n)


@lru_cache(maxsize=256)
def embed_query(text):
    """質問文を1本のベクトルにする（同じ質問文は使い回す。戻り値は書き換えないこと）"""
    res = _client().embeddings.create(model=MODEL, input=[text])
    return normalize(res.data[0].embedding)


def embed_texts(texts, chunk=100):
    """文章のリストを、そのままのベクトル（DBに入れる形）のリストにする。看板の埋め込みに使う"""
    out = []
    for start in range(0, len(texts), chunk):
        res = _client().embeddings.create(model=MODEL, input=texts[start:start + chunk])
        out += [d.embedding for d in res.data]
    return out
