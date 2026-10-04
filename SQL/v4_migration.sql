-- =============================================================
-- schema_v4_migration.sql — スキーマ v3 → v4 の変更（Supabase の SQL Editor で実行）
--
-- 2026-10 時点の実際のDB（制約の名前を含む）を確認したうえで作成。
-- 1つのトランザクションで実行するので、途中で失敗したら全部取り消される。
--
-- 変更の内容：
--   1. 社員の在籍フラグ（is_active）
--   2. アイデア投稿（doc_type = 'idea'、doc_category = '7-5'、役割「投稿者」）
--   3. 全文検索（PGroonga と、分かち書きした本文の列 body_tokens）
--   4. 文書のキーワード（document_keywords）
--   5. 看板のベクトル（profiles の embedding など）
-- =============================================================
begin;

-- ---------- 1. 社員の在籍フラグ ----------
-- 既存の社員は全員「在籍中（true）」で入る。退職者の設定は、社員マスタを作り直すときに行う
alter table employees add column if not exists is_active boolean not null default true;

-- ---------- 2. アイデア投稿 ----------
-- 文書の種類に idea を加える
alter table documents drop constraint documents_doc_type_check;
alter table documents add constraint documents_doc_type_check
    check (doc_type in ('proposal', 'project', 'idea'));

-- 種類と分類の組み合わせに idea（7-5）を加える
alter table documents drop constraint documents_check;
alter table documents add constraint documents_check check (
       (doc_type = 'proposal' and doc_category = '1-4')
    or (doc_type = 'project'  and doc_category = '2-1')
    or (doc_type = 'idea'     and doc_category = '7-5')
);

-- アイデア投稿：投稿日（submitted_at）は必須、提案区分は任意、判定とPJの項目は空
alter table documents add constraint documents_idea_check check (
    doc_type <> 'idea' or (
        submitted_at is not null
        and result is null and decided_at is null and result_reason is null
        and pj_status is null and started_at is null and planned_end_at is null
        and ended_at is null and owner_dept_id is null and updated_at is null
    )
);

-- 著者の役割に「投稿者」を加える
alter table document_authors drop constraint document_authors_role_check;
alter table document_authors add constraint document_authors_role_check
    check (role in ('提案者', '責任者', '主担当', '副担当', '投稿者'));

-- ---------- 3. 全文検索（PGroonga） ----------
-- 分かち書きした本文の列。中身は Python 側（search/tokenizer.py の index_text）で作って入れる
alter table document_sections add column if not exists body_tokens text;

create extension if not exists pgroonga with schema extensions;

-- 空白で区切られた語を、そのまま1語として扱う（TokenDelimit）
create index if not exists document_sections_body_tokens_pgroonga
    on document_sections using pgroonga (body_tokens)
    with (tokenizer = 'TokenDelimit');

-- ---------- 4. 文書のキーワード ----------
-- 画面の1段目に出すキーワード。中身は Python 側（search/tokenizer.py の keywords）で作って入れる
create table if not exists document_keywords (
    doc_id   text not null references documents (doc_id) on delete cascade,
    keyword  text not null,
    count    integer not null check (count > 0),
    primary key (doc_id, keyword)
);
create index if not exists document_keywords_keyword_idx on document_keywords (keyword);

-- ---------- 5. 看板のベクトル ----------
alter table profiles add column if not exists embedding vector(1536);
alter table profiles add column if not exists embedding_model text;
alter table profiles add column if not exists embedded_at timestamptz;

commit;

-- ---------- 確認（実行後に流す） ----------
-- select conname, pg_get_constraintdef(oid) from pg_constraint
--  where conname in ('documents_doc_type_check', 'documents_check', 'documents_idea_check', 'document_authors_role_check');
-- select extname, extversion from pg_extension where extname = 'pgroonga';
