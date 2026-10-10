# データフロー図

隠れ出る杭検索で、データがどこから来て、どこに入り、どう使われるかをまとめた図です。
部品の構成は [architecture.md](architecture.md)、点数の計算の詳しい中身は [検索アルゴリズム解説.md](検索アルゴリズム解説.md)・[仲間を探す機能アルゴリズム解説.md](仲間を探す機能アルゴリズム解説.md) を見てください。

- 更新日：2026-10-10（DB v5、検索・仲間を探す・個人看板の完成を反映）
- 色：緑＝登録（まよりん）／紫＝検索・看板（ほりえもん）

## 1. 全体

入ってくるデータは3種類（文書・キャリアシート・マスタ）。DBの中で「事実データ」から「看板」を作り、3つの使い方（人を探す・仲間を探す・もっと調べる）で読む。

```mermaid
flowchart LR
    subgraph IN["入ってくるデータ"]
        W["Word文書<br>改善提案・PJ文書"]
        J["初期データ（JSON）<br>文書・部署・社員"]
        C["キャリアシート<br>本人が書く3項目"]
    end

    subgraph DB["Supabase"]
        direction TB
        subgraph FACT["事実データ"]
            T_DOC[("documents 文書<br>document_sections 章・全文の列・埋め込み<br>document_authors 著者<br>document_keywords キーワード")]
            T_MST[("departments 部署<br>employees 社員")]
        end
        subgraph AI["看板（AI生成物）"]
            T_PRF[("profile_notes ノート<br>profiles 看板・埋め込み")]
        end
    end

    W --> REG["登録タブ"]
    REG --> T_DOC
    J --> LOAD["初期ロード"]
    LOAD --> T_DOC & T_MST
    C --> LOAD
    LOAD -- "本人入力のノート" --> T_PRF
    T_DOC -- "文書の抜粋のノート" --> T_PRF

    Q["相談したいこと"] --> S1["人を探す"]
    T_DOC --> S1
    S1 --> O1["実績のある人<br>隠れた杭"]

    N["社員番号"] --> S2["仲間を探す"]
    T_PRF --> S2
    S2 --> O2["考えの近い人"]

    O1 & O2 --> S3["もっと調べる"]
    T_PRF --> S3
    S3 --> O3["AIの紹介文<br>（出典つき）"]

    classDef reg fill:#E1F5EE,stroke:#0F6E56,color:#0F6E56
    classDef srch fill:#EEEDFE,stroke:#534AB7,color:#534AB7
    class REG reg
    class S1,S2,S3 srch
```

## 2. 初期ロード（最初に1回。実行済み）

```mermaid
flowchart LR
    J["db_load_673.json<br>追加分 db_load_add_*.json"] --> LM["load_masters.py"] --> T1[("departments<br>employees")]
    J --> LD["load_documents.py"] --> T2[("documents・document_sections<br>document_authors・document_keywords")]
    T2 -- "埋め込みが空の章" --> ES["embed_sections.py"] -- "章のベクトル" --> T2
    CS["career_sheets_mock.json<br>（モック）"] --> LC["load_career_sheets.py"] --> T3[("profile_notes<br>（本人入力）")]
    T2 & T3 --> BP["build_profiles.py"] --> T4[("profile_notes（文書の抜粋）<br>profiles（看板・埋め込み）")]
    ES <--> OPENAI["OpenAI<br>埋め込み"]
    BP <--> OPENAI
```

順番：マスタ → 文書 → 章の埋め込み → キャリアシート → 看板。看板は文書とキャリアシートの両方から作るので、最後に作る。

## 3. 登録タブ（Wordを1件ずつ足す）

```mermaid
flowchart TD
    A["Wordをアップロード<br>（複数まとめてもよい）"] --> B["読む<br>表題・文書情報の表・関係者の表・見出し1の章"]
    B --> C{"入力のチェック"}
    C -- "問題あり" --> X["❌ 理由を表示<br>登録ボタンを出さない"]
    C -- "問題なし" --> D{"DBに同じ文書番号<br>（doc_code）がある？"}
    D -- "ない" --> F["登録ボタン"]
    D -- "ある" --> E["⚠️ 上書きの注意<br>「上書きする」にチェックを入れると押せる"]
    E --> F
    F --> G["documents<br>（doc_code で上書き。doc_id はDBが振り、変わらない）"]
    G --> H["document_sections<br>（doc_id＋章番号で上書き。章のIDは変えない）<br>全文検索の列（body_tokens）も作る。減った章は削除"]
    H --> I["document_authors<br>（doc_id＋emp_id で上書き）。減った関係者は削除"]
    I --> KW["document_keywords<br>（SudachiPy で選んだ語を入れ直す）"]
    KW --> K["章を埋め込む（OpenAI）<br>「表題：／章：／本文：」の形"]
    K --> P["関係者の看板を作り直す<br>profile_notes・profiles（埋め込みも）"]
    P --> CC["検索のキャッシュを消す"]
    CC --> Z["✅ 登録しました<br>すぐ「人を探す」「仲間を探す」に出る"]

    classDef reg fill:#E1F5EE,stroke:#0F6E56,color:#0F6E56
    class G,H,I,KW,K,P,CC reg
```

入力のチェックで見ている内容：

- ファイルの形式とサイズ（10MBまで）、文書番号の形（`KZ-2026-0001` など）
- DBに同じ文書番号があるか（あれば上書きの確認）、同時に入れた文書どうしで番号が重なっていないか
- 必須項目、日付の形、提案区分・判定・状態の値
- 文字数の上限（表題200字・判定理由500字・章名50字・本文5000字）、判定と判定日・状態と終了日の組み合わせ
- 部署と社員番号がマスタにあるか、氏名がマスタと違わないか
- 章の名前（役割が決まっている章だけ）、本文が空でないか

## 4. 人を探す（相談文から）

```mermaid
flowchart LR
    Q["相談したいこと"] --> E["ベクトルにする<br>（OpenAI）"]
    Q --> M["語に分ける<br>（SudachiPy）"]
    E --> V["ベクトル検索<br>章の埋め込みと比べる"]
    M --> F["全文検索<br>章の body_tokens（PGroonga）"]
    V & F --> R["RRFで統合<br>章の点数"]
    R --> D["章 → 文書 → 人<br>document_authors の役割で重み"]
    D --> A["実績のある人<br>完了PJ・採択の提案"]
    D --> B["隠れた杭<br>それ以外（審査中・不採択・保留<br>アイデア・進行中のPJ など）"]

    classDef srch fill:#EEEDFE,stroke:#534AB7,color:#534AB7
    class V,F,R,D srch
```

起動したときに、章の埋め込み・文書・著者・社員をDBからまとめて読み、手元に置いておく（キャッシュ）。登録タブで文書を足すと、このキャッシュを消して読み直す。

## 5. 仲間を探す・もっと調べる（看板から）

```mermaid
flowchart LR
    N["社員番号"] --> PV["その人の看板のベクトル<br>（profiles）"]
    PV --> SIM["全員の看板との近さ<br>同じ部署の係数・文書の重なり"]
    SIM --> O2["考えの近い人<br>（足切り・上位N人）"]

    PICK["選んだ1人<br>（人を探す・仲間を探す の結果から）"] --> G["ノートを集める<br>profile_notes・質問に当たった章"]
    G --> LLM["紹介文を書く<br>（gpt-4o-mini）"]
    LLM --> VF["出典を確かめる<br>材料にない出典の項目は捨てる"]
    VF --> O3["紹介文<br>得意分野・意欲・実績・留意点"]

    classDef srch fill:#EEEDFE,stroke:#534AB7,color:#534AB7
    class SIM,G,LLM,VF srch
```

看板の文章そのものは画面に出さない（非公開のメモ）。紹介文は、ボタンを押したときだけ作る。

## 6. Wordの項目と、入るテーブル

| Wordの場所 | 入るテーブル | 主な列 |
|---|---|---|
| 表題、文書情報の表 | documents | doc_code（Wordの「文書ID」）、doc_type、doc_category、title、submitted_at、result、pj_status、owner_dept_id など |
| 見出し1で区切った章 | document_sections | section_no、section_name、section_role、body、body_tokens、embedding |
| 提案者（体制）の表 | document_authors | emp_id（社員番号から引く）、role、dept_id_at_time |
| 表題と全章の本文 | document_keywords | keyword、count |

章の名前と役割の対応：

| 種類 | 章の名前 → 役割 |
|---|---|
| 改善提案 | 現状→背景／問題点→課題意識／提案内容→行動案／期待効果→目標／実施上の課題→実施上の課題 |
| PJ文書 | 背景→背景／目的→目的／実施内容→行動／成果→成果 |

## 7. 未定のこと

- 登録タブでアイデア投稿（IDEA-）を扱うか。今は初期ロードでだけ入れている
- キャリアシートを画面から登録・更新するか。今はモックのJSONを初期ロードで入れている
- 看板のノートを、抜粋から要約（GPT）に変えるか。列（body_source＝要約）は用意済み
