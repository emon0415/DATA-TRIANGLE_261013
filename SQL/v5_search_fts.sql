-- =============================================================
-- v5_search_fts.sql — 全文検索（PGroonga）の検索関数
--
-- 前提：v5_create.sql を実行済み（body_tokens の列と PGroonga の索引がある）
-- 呼び出し：search/retrieve.py の fulltext_search()（supabase の rpc）
--
-- q には tokenizer.query_text() の出力を渡す（例："昇降機" OR "遠隔監視"）
-- 戻り値は、スコアの高い順に n 件の「章のID」と「スコア」
-- =============================================================
create or replace function search_sections_fts(q text, n int default 100)
returns table (section_id bigint, score double precision)
language sql
stable
set search_path = public, extensions
as $$
    select s.section_id,
           pgroonga_score(s.tableoid, s.ctid)::double precision as score
    from document_sections s
    where s.body_tokens &@~ q
    order by score desc
    limit greatest(n, 0);
$$;

-- 動作確認（SQL Editor で）
--   select * from search_sections_fts('"昇降機" OR "遠隔監視"', 5);
-- 索引が使われているか
--   explain select 1 from document_sections where body_tokens &@~ '"昇降機"';
--   → Index Scan using document_sections_body_tokens_pgroonga が出ればよい
-- 権限：アプリの鍵（anon／service_role）から呼べるかは、RLSの設定による。
--   呼べない場合は  grant execute on function search_sections_fts(text, int) to anon;
