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


def embed_query(text):
    """質問文を1本のベクトルにする"""
    res = _client().embeddings.create(model=MODEL, input=[text])
    return normalize(res.data[0].embedding)
