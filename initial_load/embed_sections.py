# =============================================================
# embed_sections.py — 章の本文をベクトルにして document_sections.embedding に書き込む
#
# 使い方（initial_load フォルダで実行）：
#   python embed_sections.py --dry-run   件数と費用の目安を出すだけ。OpenAIもDBも使わない
#   python embed_sections.py             埋め込みを作ってDBに書き込む（OpenAIの課金あり）
#
# 前提：
#   ・load_documents.py で文書と章が入っていること
#   ・data-triangle フォルダ直下の .env に SUPABASE_URL、SUPABASE_KEY、OPENAI_API_KEY を書く
#   ・embedding が空の章だけを処理するので、途中で止まってもやり直せる
#
# ベクトルにする文章は、検証（_test/search_eval/verify_analysis.py）と同じ形にそろえる：
#   「表題：〇〇／章：〇〇／本文：〇〇」
#   検索のときの質問文も、同じモデルでベクトルにすること
# =============================================================
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
MODEL = "text-embedding-3-small"  # 1536次元。DBの列 VECTOR(1536) と合わせる
PRICE_PER_1M = 0.02               # ドル／100万トークン（2026年9月時点の目安）
CHUNK = 100                       # 一度に埋め込む章の数
PAGE = 1000                       # DBから一度に読む行数


def section_text(row):
    return f"表題：{row['documents']['title']}／章：{row['section_name']}／本文：{row['body']}"


def fetch_todo(sb):
    """embedding が空の章を、文書の表題と一緒にすべて読む"""
    cols = "section_id,doc_id,section_no,section_name,section_role,body,documents(title)"
    out, start = [], 0
    while True:
        res = (sb.table("document_sections").select(cols)
               .is_("embedding", "null").order("section_id")
               .range(start, start + PAGE - 1).execute())
        out += res.data
        if len(res.data) < PAGE:
            return out
        start += PAGE


def main():
    dry_run = "--dry-run" in sys.argv

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(HERE.parent / ".env")
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        sys.exit(".env に SUPABASE_URL と SUPABASE_KEY が見つかりません。")
    sb = create_client(url, key)

    rows = fetch_todo(sb)
    if not rows:
        print("embedding が空の章はありません。終了します。")
        return
    texts = [section_text(r) for r in rows]
    chars = sum(len(t) for t in texts)
    # 日本語はおおむね1文字＝1トークン強。多めに見積もる
    cost = chars * 1.5 / 1_000_000 * PRICE_PER_1M
    print(f"embedding が空の章：{len(rows)}件（{chars:,}文字、費用の目安 約${cost:.3f}）")
    print(f"例：{texts[0][:80]}…")

    if dry_run:
        print("\n--dry-run のため、ここで終了します（OpenAIもDBも使っていません）。")
        return

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(".env に OPENAI_API_KEY が見つかりません。")
    from openai import OpenAI
    client = OpenAI()

    done = 0
    for start in range(0, len(rows), CHUNK):
        part = rows[start:start + CHUNK]
        res = client.embeddings.create(model=MODEL, input=texts[start:start + CHUNK])
        now = datetime.now(timezone.utc).isoformat()
        payload = []
        for r, e in zip(part, res.data):
            payload.append({  # section_id は自動採番（GENERATED ALWAYS）なので送らない
                "doc_id": r["doc_id"],
                "section_no": r["section_no"],
                "section_name": r["section_name"],
                "section_role": r["section_role"],
                "body": r["body"],
                "embedding": e.embedding,
                "embedding_model": MODEL,
                "embedded_at": now,
            })
        # 「文書ID＋章番号」で上書き
        sb.table("document_sections").upsert(payload, on_conflict="doc_id,section_no").execute()
        done += len(part)
        print(f"   {done}/{len(rows)}件 書き込み")

    left = (sb.table("document_sections").select("*", count="exact", head=True)
            .is_("embedding", "null").execute().count)
    print(f"完了。embedding がまだ空の章：{left}件")


if __name__ == "__main__":
    main()
