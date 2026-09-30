# データフロー図

隠れ出る杭検索で、データがどこから来て、どこに入り、どう使われるかをまとめた図です。
点線の枠は、まだ作っていない部分（予定）です。

- 更新日：2026-09-30
- 登録タブ：まよりん（実装済み）／検索タブ：ほりえもん（作成中）

## 1. 全体（普段の使い方）

```mermaid
flowchart LR
    W["Word文書（.docx）<br>改善提案書・PJ文書"] --> REG["登録タブ<br>register/page.py"]
    Q["相談したいこと<br>（検索する人が書く文章）"] --> SEARCH["検索タブ<br>（作成中）"]

    REG -- "文書・章・関係者・埋め込みを書き込む" --> DB
    DB -- "部署・社員マスタで照合" --> REG
    REG <-- "章の文章 → ベクトル" --> OPENAI["OpenAI<br>text-embedding-3-small"]

    DB -.-> SEARCH
    SEARCH <-. "相談文 → ベクトル" .-> OPENAI
    SEARCH -.-> OUT["検索結果<br>実績のある人・隠れた杭<br>（人と根拠の文書）"]

    subgraph DB["Supabase"]
        direction TB
        T_DOC[("documents<br>文書")]
        T_SEC[("document_sections<br>章・埋め込み")]
        T_AUT[("document_authors<br>関係者")]
        T_EMP[("employees<br>社員")]
        T_DEPT[("departments<br>部署")]
    end

    classDef todo stroke-dasharray: 5 5
    class SEARCH,OUT todo
```

## 2. 初期ロード（最初に1回だけ。実行済み）

```mermaid
flowchart LR
    M["部署・社員マスタ<br>departments.json<br>employees.json"] --> LM["load_masters.py"] --> T_MASTER[("departments<br>employees")]
    J["初期データ 373件<br>db_load_373.json"] --> LD["load_documents.py"] --> T_DOCS[("documents<br>document_sections<br>document_authors")]
    T_DOCS -- "埋め込みが空の章" --> ES["embed_sections.py"]
    ES <-- "章の文章 → ベクトル" --> OPENAI["OpenAI"]
    ES -- "埋め込み" --> T_DOCS
```

## 3. 登録タブ（Wordを登録する流れ）

```mermaid
flowchart TD
    A["Wordをアップロード<br>（複数まとめてもよい）"] --> B["読む<br>表題・文書情報の表・関係者の表・見出し1の章"]
    B --> C{"入力のチェック"}
    C -- "問題あり" --> X["❌ 理由を表示<br>登録ボタンを出さない"]
    C -- "問題なし" --> D{"DBに同じ文書IDがある？"}
    D -- "ない" --> F["登録ボタン"]
    D -- "ある" --> E["⚠️ 上書きの注意<br>「上書きする」にチェックを入れると押せる"]
    E --> F
    F --> G["documents に書き込む<br>（文書IDで上書き）"]
    G --> H["document_sections に書き込む<br>（文書ID＋章番号で上書き。章のIDは変えない）<br>減った章は削除"]
    H --> I["document_authors に書き込む<br>（文書ID＋社員番号で上書き）<br>減った関係者は削除"]
    I --> K["章を埋め込む（OpenAI）<br>「表題：／章：／本文：」の形"]
    K --> L["document_sections.embedding に書き込む"]
    L --> Z["✅ 登録しました"]
```

入力のチェックで見ている内容：

- ファイルの形式とサイズ（10MBまで）、文書IDの形（`KZ-2026-0001` など）
- 必須項目、日付の形、提案区分・判定・状態の値（今のDBにある値だけ）
- 部署と社員番号がマスタにあるか、氏名がマスタと違わないか
- 章の名前（役割が決まっている章だけ）、本文が空でないか
- 同時に入れた文書どうしで文書IDが重なっていないか

## 4. Wordの項目と、入るテーブル

| Wordの場所 | 入るテーブル | 主な列 |
|---|---|---|
| 表題、文書情報の表 | documents | doc_id、doc_type、doc_category、title、submitted_at、result、pj_status、owner_dept_id など |
| 見出し1で区切った章 | document_sections | section_no、section_name、section_role、body、embedding |
| 提案者（体制）の表 | document_authors | emp_no、role、dept_id_at_time |

章の名前と役割の対応：

| 種類 | 章の名前 → 役割 |
|---|---|
| 改善提案 | 現状→背景／問題点→課題意識／提案内容→行動案／期待効果→目標／実施上の課題→実施上の課題 |
| PJ文書 | 背景→背景／目的→目的／実施内容→行動／成果→成果 |

## 未定のこと

- アイデア投稿（`doc_type = idea`）の取り込み：仕様を相談中
- 登録のときのキーワード抽出（SudachiPy）：画面のモックにはあるが、登録タブでは未実装。検索側とどこで行うかを相談する
- 検索タブの中身（ベクトル検索とキーワード検索の組み合わせ、推薦理由の作り方）：ほりえもんさんが作成中
