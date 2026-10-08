-- =============================================================
-- v5 追加：看板のノート（profile_notes）
--
-- 看板（profiles）は 1人1行。その中身になるノートを、1人に何枚も持てる別の表にする。
--   ・文書由来のノート … doc_id に元の文書を持つ（根拠の出典になる）
--       種類は documents.doc_type で決まる（proposal／idea＝意欲、project＝実行力）。
--       役割・結果・日付は、doc_id から document_authors／documents を引く（コピーしない）
--   ・カルテのノート   … 本人の入力。doc_id は空
--
-- 既存の表には、profiles.model を空にできるようにする変更だけを加える（末尾）。Supabase の SQL Editor で、そのまま実行できる（再実行しても止まらない）。
-- 新しいDBを作る場合は、v5_create.sql にも同じ定義が入っている（この追加分は不要）。
-- =============================================================
create table if not exists profile_notes (
    note_id     bigint generated always as identity primary key,
    emp_id      bigint not null references employees (emp_id) on delete cascade,
    doc_id      bigint references documents (doc_id) on delete cascade,   -- カルテは空
    body        text   not null check (char_length(btrim(body)) between 1 and 1000),
    body_source text   not null check (body_source in ('抜粋', '要約', '本人入力')),
    model       text   check (char_length(btrim(model)) between 1 and 100),   -- 要約のときだけ
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    -- カルテ（本人入力）だけが、文書を持たない
    check ((doc_id is null) = (body_source = '本人入力')),
    -- 要約のときだけ、要約したモデル名を持つ
    check ((body_source = '要約') = (model is not null)),
    -- 文書由来のノートは、1人・1文書につき1枚（doc_id が空のカルテは何枚でも持てる）
    unique (emp_id, doc_id)
);

-- 文書を消したときのノートの削除と、文書からノートを引くための索引
-- （emp_id からの検索は、unique (emp_id, doc_id) の索引で足りる）
create index if not exists profile_notes_doc_idx on profile_notes (doc_id) where doc_id is not null;

-- 看板（profiles）のモデル名を、空にできるようにする（ノートを並べただけの看板にはモデルがない）
-- 新しいDBを作る場合は、v5_create.sql に反映済み
alter table profiles alter column model drop not null;

-- =============================================================
-- キャリアシート（本人入力のノート）と、看板の文章の上限
-- =============================================================
-- 本人入力のノートの種類。キャリアシートの3項目（各1000字まで）のどれか。文書由来のノートは空
alter table profile_notes add column if not exists memo_kind text;
-- 項目名の check は付け直す（前の版の項目名で作っていても、再実行で新しい項目名になる）
alter table profile_notes drop constraint if exists profile_notes_memo_kind_check;
alter table profile_notes add constraint profile_notes_memo_kind_check
    check (memo_kind in ('現在の職務', '将来やりたいこと', 'そのために取り組んでいること'));
alter table profile_notes drop constraint if exists profile_notes_memo_kind_source_check;
alter table profile_notes add constraint profile_notes_memo_kind_source_check
    check ((memo_kind is not null) = (body_source = '本人入力'));
-- 1人につき、各種類1枚（更新したら上書き。文書由来のノートは memo_kind が空なので、何枚でも持てる）
alter table profile_notes drop constraint if exists profile_notes_emp_memo_uq;
alter table profile_notes add constraint profile_notes_emp_memo_uq unique (emp_id, memo_kind);

-- 看板の文章の上限を、3000字から6000字に広げる（キャリアシート3項目で最大3000字になるため）
alter table profiles drop constraint if exists profiles_profile_text_check;
alter table profiles add constraint profiles_profile_text_check
    check (char_length(btrim(profile_text)) between 1 and 6000);
