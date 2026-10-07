# =============================================================
# build_profiles.py — 看板のノートと文章を作って、DBに書く（初期ロード用）
#
# 使い方（リポジトリ直下で実行）：
#   python initial_load/build_profiles.py --dry-run   DBは読むだけ。人数・長さ・例を出す（書かない・課金なし）
#   python initial_load/build_profiles.py --no-embed  ノートと看板の文章だけ書く（埋め込みは作らない）
#   python initial_load/build_profiles.py             ノート・看板・埋め込みをすべて書く
#
# 前提：
#   ・SQL/v5_profile_notes.sql を SQL Editor で実行済み（profile_notes がある）
#   ・文書・章・著者・キーワードが入っている（initial_load/ の他のスクリプトを実行済み）
#   ・.env に SUPABASE_URL、SUPABASE_KEY、（埋め込みを作るなら）OPENAI_API_KEY
#
# 何度実行してもよい。文章が変わった人の埋め込みだけを作り直す。
# 埋め込みの課金は、551人分でも1円未満（text-embedding-3-small）。文章の生成（GPT）は使わない。
# =============================================================
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from register import db                # noqa: E402
from search import profiles_store      # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


def main():
    dry_run, embed = "--dry-run" in sys.argv, "--no-embed" not in sys.argv
    sb = db.connect()
    r = profiles_store.rebuild(sb, embed=embed, dry_run=dry_run)
    built = r["built"]
    lens = sorted(len(v["text"]) for v in built.values())
    print(f"看板を作る人：{r['people']}人／ノート：{r['notes']}枚（文書由来）")
    if lens:
        print(f"看板の文章の長さ：中央 {lens[len(lens) // 2]}字／最大 {lens[-1]}字（上限 3000字）")
    cut = sum(1 for v in built.values() if v["used"] < len(v["notes"]))
    print(f"長すぎて古いノートを外した人：{cut}人")
    print(f"書き込む看板（新規または文章が変わった人）：{r['profiles']}人")
    if built:
        emp = max(built, key=lambda e: len(built[e]["notes"]))
        print(f"\n--- 例：ノートがいちばん多い人（社員ID {emp}、{len(built[emp]['notes'])}枚）---")
        print(built[emp]["text"][:1200])
    if dry_run:
        print("\n--dry-run のため、ここで終了します（何も書いていません）。")
        return
    print(f"\n書き込み完了。埋め込みを作った人：{r['embedded']}人")


if __name__ == "__main__":
    main()
