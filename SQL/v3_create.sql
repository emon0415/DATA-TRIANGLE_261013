-- =============================================================
-- 隠れ出る杭検索 物理モデル v3（Supabase / PostgreSQL）
-- PoC用。SQL Editor に貼り付けて上から実行する
-- 業種設定：産業用装置メーカー（テクゼロン工業／従業員3,000名）
--
-- 方針
--   ・提案とPJは documents 1テーブルで管理（本番ではスーパータイプへ作り直す想定）
--   ・本文とベクトルは document_sections に1章1行で持つ
--   ・行単位アクセス制御（RLS）はPoCの範囲外
--   ・検索ログ（search_logs）は意図的に作らない
--
-- 想定規模（模擬データ）
--   部門31／社員620名／提案書 約1,400件／プロジェクト文書 約180件／章 約7,000行
-- =============================================================

create extension if not exists vector;

-- -------------------------------------------------------------
-- 事実データ
-- -------------------------------------------------------------
create table departments (
    dept_id    text primary key,
    dept_name  text not null,
    dept_type  text not null check (dept_type in ('本社', '工場', '支店・営業所', 'その他'))
);

create table employees (
    emp_no   text primary key,                 -- 先頭0を保つため text
    name     text not null,
    dept_id  text not null references departments (dept_id),
    site     text,
    title    text
);

create table documents (
    doc_id         text primary key,          -- 例: KZ-2024-0147 / PJ-2023-0089
    doc_type       text not null check (doc_type in ('proposal', 'project')),
    doc_category   text not null,             -- 一覧の中分類（1-4 業務改善提案、2-1 プロジェクト文書）
    title          text not null,
    source_file    text not null,             -- 元のWordファイル名（監査用）

    -- 提案のみ
    submitted_at   date,
    -- 提案区分は全社共通。改善の効果で切るため、工場・本社・営業のどの部署でも使える
    proposal_area  text check (proposal_area in
                       ('品質', 'コスト', '納期・スピード', '安全・環境', '業務効率', 'その他')),
    result         text check (result in ('審査中', '採択', '不採択', '保留')),
    decided_at     date,
    result_reason  text,

    -- PJのみ
    pj_status      text check (pj_status in ('計画中', '進行中', '完了', '中止')),
    started_at     date,
    planned_end_at date,
    ended_at       date,
    owner_dept_id  text references departments (dept_id),
    updated_at     date,

    -- 業務分類は文書種別と対応させる
    check (
        (doc_type = 'proposal' and doc_category = '1-4')
     or (doc_type = 'project'  and doc_category = '2-1')
    ),
    -- 提案：提案の項目は必須、PJの項目は空
    check (
        doc_type <> 'proposal' or (
            submitted_at is not null and proposal_area is not null and result is not null
            and pj_status is null and started_at is null and planned_end_at is null
            and ended_at is null and owner_dept_id is null and updated_at is null
        )
    ),
    -- PJ：PJの項目は必須、提案の項目は空
    check (
        doc_type <> 'project' or (
            pj_status is not null and started_at is not null and planned_end_at is not null
            and owner_dept_id is not null and updated_at is not null
            and submitted_at is null and proposal_area is null and result is null
            and decided_at is null and result_reason is null
        )
    ),
    -- 審査中の提案には判定日を入れない
    check (result is distinct from '審査中' or decided_at is null),
    -- 終了日は完了・中止のときだけ入る
    check (
        doc_type <> 'project'
        or (pj_status in ('完了', '中止') and ended_at is not null)
        or (pj_status in ('計画中', '進行中') and ended_at is null)
    )
);

create table document_sections (
    section_id      bigint generated always as identity primary key,
    doc_id          text not null references documents (doc_id) on delete cascade,
    section_no      int  not null,
    section_name    text not null,             -- 例: 問題点、実施内容
    section_role    text not null check (section_role in (
                        '背景', '課題意識', '行動案', '目標', '実施上の課題',  -- 提案
                        '目的', '行動', '成果'                                -- PJ（背景は共通）
                    )),
    body            text not null,             -- 表は除き、キャプションのみ含める
    embedding       vector(1536),              -- 表題と章名を先頭に付けた文から生成
    embedding_model text,
    embedded_at     timestamptz,
    unique (doc_id, section_no)
);

create table document_authors (
    doc_id          text not null references documents (doc_id) on delete cascade,
    emp_no          text not null references employees (emp_no),
    role            text not null check (role in ('提案者', '責任者', '主担当', '副担当')),
    dept_id_at_time text not null references departments (dept_id),
    primary key (doc_id, emp_no)
    -- dept_id_at_time は作成時の所属。PoCでは異動履歴を持たないため、
    -- 取り込み時に employees.dept_id（現在の所属）と同じ値を入れる。
    -- 将来、異動・所属履歴（5-5）を取り込んだ時点で正しい値に置き換える。
    --
    -- 提案は提案者1名のみ、PJは責任者1名・主担当1名・副担当0名以上。
    -- 文書種別との組み合わせは取り込み処理で検証する（PoCではDB制約にしない）
);

-- -------------------------------------------------------------
-- AI生成物（人についての推定を含むため事実データと分離）
-- -------------------------------------------------------------
create table profiles (
    emp_no           text primary key references employees (emp_no) on delete cascade,
    profile_text     text not null,
    source_doc_count int  not null,
    model            text not null,
    generated_at     timestamptz not null default now()
);

create table tags (
    tag_id        bigint generated always as identity primary key,
    emp_no        text not null references employees (emp_no) on delete cascade,
    tag_name      text not null,
    score         real check (score between 0 and 1),
    source_doc_id text not null references documents (doc_id) on delete cascade,  -- 1行1根拠。PoCでは文書のみ
    model         text not null,
    generated_at  timestamptz not null default now()
);

-- -------------------------------------------------------------
-- 分析
--   duplicate：分析B 車輪の再発明（優先）
--   issue    ：分析A 同じ問題意識の集まり（余裕があれば）
-- -------------------------------------------------------------
create table cluster_runs (
    run_id          bigint generated always as identity primary key,
    analysis_type   text   not null check (analysis_type in ('duplicate', 'issue')),
    target_roles    text[] not null,           -- 例: {行動案,目的,行動} / {課題意識}
    threshold       real   not null check (threshold > 0 and threshold <= 1),
    min_size        int    not null,           -- 分析Bは2、分析Aは4
    min_depts       int,                       -- 分析Aのみ（2）。分析Bは空
    embedding_model text   not null,
    executed_at     timestamptz not null default now(),
    check (analysis_type = 'issue' or min_depts is null)
);

create table clusters (
    cluster_id     bigint generated always as identity primary key,
    run_id         bigint  not null references cluster_runs (run_id) on delete cascade,
    size           int     not null,           -- 異なる文書の数（同じ文書の章同士は数えない）
    dept_count     int     not null,
    min_similarity real    not null,           -- 塊内の最低類似度。鎖効果の確認用
    is_valid       boolean not null            -- 成立条件を満たしたか
);

create table cluster_members (
    cluster_id  bigint not null references clusters (cluster_id) on delete cascade,
    section_id  bigint not null references document_sections (section_id) on delete cascade,
    primary key (cluster_id, section_id)
);

-- -------------------------------------------------------------
-- 利用
-- -------------------------------------------------------------
create table app_users (
    user_id     uuid primary key references auth.users (id) on delete cascade,
    emp_no      text not null unique references employees (emp_no),
    role        text not null default 'general' check (role in ('general', 'manager', 'admin')),
    created_at  timestamptz not null default now()
);

-- 定義のみ。PoCでは使わない（段階③の機能）
create table interests (
    from_emp_no  text not null references employees (emp_no) on delete cascade,
    to_emp_no    text not null references employees (emp_no) on delete cascade,
    source_query text,
    created_at   timestamptz not null default now(),
    primary key (from_emp_no, to_emp_no),
    check (from_emp_no <> to_emp_no)
);

-- 検索ログ（search_logs）は意図的に作成しない。
-- 誰が誰を探したかを記録しないことで、検索する側の心理的安全性を担保する。

-- -------------------------------------------------------------
-- インデックス
-- -------------------------------------------------------------
-- 数百件なら全件比較でも十分速い。件数が増えた場合に備えて定義しておく
create index document_sections_embedding_idx
    on document_sections using hnsw (embedding vector_cosine_ops);

create index document_sections_doc_idx   on document_sections (doc_id);
create index document_sections_role_idx  on document_sections (section_role);
create index document_authors_emp_idx    on document_authors (emp_no);
create index documents_type_idx          on documents (doc_type);
create index tags_emp_idx                on tags (emp_no);
create index clusters_run_idx            on clusters (run_id);
create index cluster_members_section_idx on cluster_members (section_id);

-- 行単位アクセス制御（RLS）はPoCの範囲外のため設定しない。
-- Supabase はRLS無効のテーブルに警告を出すが、PoCでは許容する。
-- アプリからの接続キーは画面側に置かず、st.secrets で管理すること。
