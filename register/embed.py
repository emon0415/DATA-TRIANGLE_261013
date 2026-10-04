# =============================================================
# embed.py — 登録した文書の章だけを埋め込んで document_sections.embedding に書き込む
#
# ベクトルにする文章とモデルは initial_load/embed_sections.py と同じにそろえること
#   「表題：〇〇／章：〇〇／本文：〇〇」、text-embedding-3-small（1536次元）
# OpenAIの課金あり（1章あたり 約0.0005円）。
# =============================================================
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
MODEL = "text-embedding-3-small"


def section_text(title, section):
    return f"表題：{title}／章：{section['section_name']}／本文：{section['body']}"


def embed_document(sb, rows):
    """1文書分の行（word_to_rows.read_word の結果）の章を埋め込み、埋め込んだ章の数を返す"""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(".env に OPENAI_API_KEY が見つかりません。")

    title = rows["documents"][0]["title"]
    sections = rows["document_sections"]
    res = OpenAI().embeddings.create(model=MODEL, input=[section_text(title, s) for s in sections])
    now = datetime.now(timezone.utc).isoformat()
    payload = [{**s, "embedding": e.embedding, "embedding_model": MODEL, "embedded_at": now}
               for s, e in zip(sections, res.data)]
    # 「文書ID＋章番号」で上書き（section_id は変わらない）
    sb.table("document_sections").upsert(payload, on_conflict="doc_id,section_no").execute()
    return len(payload)
