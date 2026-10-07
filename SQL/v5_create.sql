-- =============================================================
-- 隠れ出る杭検索 物理モデル v5（Supabase / PostgreSQL）
-- v4までの変更を含めて作り直す（DB入れ替え前のため migration ではなく create）
--
-- v5の方針
--   ・主キーは text を使わない（bigint identity。app_users のみ認証の uuid）
--   ・人が読む番号は *_code / emp_no として別の列に持ち、unique ＋ 形式 check
--     （Wordの読み取りミスは、番号の修正だけで直せる。子テーブルは影響を受けない）
--   ・text 列には長さと空文字の check を付ける
-- =============================================================
create extension if not exists vector;
create extension if not exists pgroonga with schema extensions;

-- ---------- 事実データ ----------
create table departments (
    dept_id    bigint generated always as identity primary key,
    dept_code  text not null unique check (dept_code ~ '^D-[A-Z0-9-]+$' and char_length(dept_code) <= 30),
    dept_name  text not null check (char_length(btrim(dept_name)) between 1 and 50),
    dept_type  text not null check (dept_type in ('本社', '工場', '支店・営業所', 'その他'))
);

create table employees (
    emp_id    bigint generated always as identity primary key,
    emp_no    text not null unique check (emp_no ~ '^[0-9]{4,5}$'),
    name      text not null check (char_length(btrim(name)) between 1 and 50),
    dept_id   bigint not null references departments (dept_id),
    site      text check (char_length(btrim(site)) between 1 and 50),
    title     text check (char_length(btrim(title)) between 1 and 50),
    is_active boolean not null default true
);

create table documents (
    doc_id         bigint generated always as identity primary key,
    doc_code       text not null unique check (doc_code ~ '^(KZ|PJ|IDEA)-[0-9]{4}-[0-9]{4}$'),
    doc_type       text not null check (doc_type in ('proposal', 'project', 'idea')),
    doc_category   text not null,
    title          text not null check (char_length(btrim(title)) between 1 and 200),
    source_file    text not null check (char_length(btrim(source_file)) between 1 and 255),

    submitted_at   date,
    proposal_area  text check (proposal_area in
                       ('品質', 'コスト', '納期・スピード', '安全・環境', '業務効率', 'その他')),
    result         text check (result in ('審査中', '採択', '不採択', '保留')),
    decided_at     date,
    result_reason  text check (char_length(btrim(result_reason)) between 1 and 500),

    pj_status      text check (pj_status in ('計画中', '進行中', '完了', '中止')),
    started_at     date,
    planned_end_at date,
    ended_at       date,
    owner_dept_id  bigint references departments (dept_id),
    updated_at     date,

    -- 文書番号の接頭辞・種類・分類を対応させる
    check (
        (doc_type = 'proposal' and doc_category = '1-4' and doc_code like 'KZ-%')
     or (doc_type = 'project'  and doc_category = '2-1' and doc_code like 'PJ-%')
     or (doc_type = 'idea'     and doc_category = '7-5' and doc_code like 'IDEA-%')
    ),
    check (
        doc_type <> 'proposal' or (
            submitted_at is not null and proposal_area is not null and result is not null
            and pj_status is null and started_at is null and planned_end_at is null
            and ended_at is null and owner_dept_id is null and updated_at is null
        )
    ),
    check (
        doc_type <> 'project' or (
            pj_status is not null and started_at is not null and planned_end_at is not null
            and owner_dept_id is not null and updated_at is not null
            and submitted_at is null and proposal_area is null and result is null
            and decided_at is null and result_reason is null
        )
    ),
    check (
        doc_type <> 'idea' or (
            submitted_at is not null
            and result is null and decided_at is null and result_reason is null
            and pj_status is null and started_at is null and planned_end_at is null
            and ended_at is null and owner_dept_id is null and updated_at is null
        )
    ),
    check (result is distinct from '審査中' or decided_at is null),
    check (
        doc_type <> 'project'
        or (pj_status in ('完了', '中止') and ended_at is not null)
        or (pj_status in ('計画中', '進行中') and ended_at is null)
    )
);

create table document_sections (
    section_id      bigint generated always as identity primary key,
    doc_id          bigint not null references documents (doc_id) on delete cascade,
    section_no      int  not null check (section_no > 0),
    section_name    text not null check (char_length(btrim(section_name)) between 1 and 50),
    section_role    text not null check (section_role in (
                        '背景', '課題意識', '行動案', '目標', '実施上の課題',
                        '目的', '行動', '成果'
                    )),
    body            text not null check (char_length(btrim(body)) between 1 and 5000),
    body_tokens     text,
    embedding       vector(1536),
    embedding_model text,
    embedded_at     timestamptz,
    unique (doc_id, section_no)
);

create table document_authors (
    doc_id          bigint not null references documents (doc_id) on delete cascade,
    emp_id          bigint not null references employees (emp_id),
    role            text not null check (role in ('提案者', '責任者', '主担当', '副担当', '投稿者')),
    dept_id_at_time bigint not null references departments (dept_id),
    primary key (doc_id, emp_id)
    -- 文書種別と役割の組み合わせは取り込み処理で検証する
);

create table document_keywords (
    doc_id   bigint not null references documents (doc_id) on delete cascade,
    keyword  text not null check (char_length(btrim(keyword)) between 1 and 50),
    count    integer not null check (count > 0),
    primary key (doc_id, keyword)
);

-- ---------- AI生成物 ----------
create table profiles (
    emp_id           bigint primary key references employees (emp_id) on delete cascade,
    profile_text     text not null check (char_length(btrim(profile_text)) between 1 and 3000),
    source_doc_count int  not null check (source_doc_count >= 0),
    model            text not null check (char_length(model) between 1 and 100),
    generated_at     timestamptz not null default now(),
    embedding        vector(1536),
    embedding_model  text,
    embedded_at      timestamptz
);

create table tags (
    tag_id        bigint generated always as identity primary key,
    emp_id        bigint not null references employees (emp_id) on delete cascade,
    tag_name      text not null check (char_length(btrim(tag_name)) between 1 and 50),
    score         real check (score between 0 and 1),
    source_doc_id bigint not null references documents (doc_id) on delete cascade,
    model         text not null check (char_length(model) between 1 and 100),
    generated_at  timestamptz not null default now()
);

-- ---------- 分析 ----------
create table cluster_runs (
    run_id          bigint generated always as identity primary key,
    analysis_type   text   not null check (analysis_type in ('duplicate', 'issue')),
    target_roles    text[] not null,
    threshold       real   not null check (threshold > 0 and threshold <= 1),
    min_size        int    not null check (min_size >= 2),
    min_depts       int,
    embedding_model text   not null,
    executed_at     timestamptz not null default now(),
    check (analysis_type = 'issue' or min_depts is null)
);

create table clusters (
    cluster_id     bigint generated always as identity primary key,
    run_id         bigint  not null references cluster_runs (run_id) on delete cascade,
    size           int     not null check (size >= 1),
    dept_count     int     not null check (dept_count >= 1),
    min_similarity real    not null,
    is_valid       boolean not null
);

create table cluster_members (
    cluster_id  bigint not null references clusters (cluster_id) on delete cascade,
    section_id  bigint not null references document_sections (section_id) on delete cascade,
    primary key (cluster_id, section_id)
);

-- ---------- 利用 ----------
create table app_users (
    user_id     uuid primary key references auth.users (id) on delete cascade,
    emp_id      bigint not null unique references employees (emp_id),
    role        text not null default 'general' check (role in ('general', 'manager', 'admin')),
    created_at  timestamptz not null default now()
);

create table interests (
    from_emp_id  bigint not null references employees (emp_id) on delete cascade,
    to_emp_id    bigint not null references employees (emp_id) on delete cascade,
    source_query text,
    created_at   timestamptz not null default now(),
    primary key (from_emp_id, to_emp_id),
    check (from_emp_id <> to_emp_id)
);

-- ---------- インデックス ----------
create index document_sections_embedding_idx on document_sections using hnsw (embedding vector_cosine_ops);
create index document_sections_body_tokens_pgroonga on document_sections using pgroonga (body_tokens)
    with (tokenizer = 'TokenDelimit');
create index document_sections_role_idx  on document_sections (section_role);
create index document_authors_emp_idx    on document_authors (emp_id);
create index document_keywords_keyword_idx on document_keywords (keyword);
create index documents_type_idx          on documents (doc_type);
create index employees_dept_idx          on employees (dept_id);
create index tags_emp_idx                on tags (emp_id);
create index clusters_run_idx            on clusters (run_id);
create index cluster_members_section_idx on cluster_members (section_id);
-- document_sections は unique (doc_id, section_no) があるので doc_id 単独の索引は不要
