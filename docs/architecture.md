# アーキテクチャ図

隠れ出る杭検索を、どんな部品（プログラム・サービス）で組み立てているかをまとめた図です。
データの流れは [data_flow.md](data_flow.md)、表の中身は [テーブル定義書.md](テーブル定義書.md) を見てください。
同じ内容を draw.io でも描いています（[architecture.drawio](architecture.drawio)。発表資料用に見た目を直せます。ページは「アーキテクチャ」と「データフロー」の2枚）。

- **見るだけなら PDF**：[architecture.pdf](architecture.pdf)（draw.io の2ページをそのまま書き出したもの。GitHub 上でもそのまま見られます）
- **直すときは draw.io**：GitHub では .drawio は図として表示されません。ファイルをダウンロードして、ブラウザで <https://app.diagrams.net> を開き「既存の図を開く」から選ぶか、VS Code の拡張機能「Draw.io Integration」で開いてください
- draw.io を直したら、PDF も書き出し直してください（draw.io の「ファイル → エクスポート → PDF」で「すべてのページ」）

- 更新日：2026-10-11（Streamlit Community Cloud での公開、つながりマップ・属人化マップ・空白地帯マップ・相談メッセージの下書きを反映）
- 色：緑＝登録（まよりん）／紫＝検索・看板・マップ（ほりえもん）／灰＝共通・外部サービス
- 点線の枠は、表だけ作って、まだ使っていない部分（予定）です

## 1. 構成

```mermaid
flowchart TB
    USER["社員<br>（ブラウザ）"]
    CLOUD["Streamlit Community Cloud（公開URL）<br>GitHub の main から起動<br>接続情報とAPIキーは Secrets に置く"]

    subgraph APP["アプリ（Streamlit・Python）　app.py：画面の切り替え"]
        direction TB
        subgraph SCREENS["6つの画面"]
            direction LR
            P3["文書を登録する<br>Wordをアップロード"]
            P1["人を探す<br>相談文から探す"]
            P2["仲間を探す<br>社員番号から、近い人"]
            P5["属人化マップ<br>替えがききにくい経験"]
            P6["空白地帯マップ<br>手つかずのテーマ"]
            P4["個人看板を検索<br>（開発用）"]
        end
        subgraph REG["登録　register/"]
            R1["page.py：画面<br>word_to_rows.py：Wordを読む・チェック<br>db.py：DBの読み書き<br>embed.py：章の埋め込み"]
        end
        subgraph SRCH["検索・看板・マップ　search/"]
            S1["service.py：人を探す のまとめ役<br>retrieve.py：ベクトル検索＋全文検索<br>rank.py：RRFで統合 → 章→文書→人<br>tokenizer.py：形態素解析（SudachiPy）<br>embed.py：質問文の埋め込み"]
            S2["similar.py：仲間を探す<br>profiles.py・profiles_store.py：看板を作る<br>person_brief.py：もっと調べる（紹介文）<br>outreach.py：相談メッセージの下書き"]
            S3["themes.py：属人化・空白地帯<br>network.py：つながりマップ（pyvis）"]
        end
    end

    subgraph EXT["外部サービス"]
        SUPA[("Supabase（PostgreSQL）<br>pgvector：ベクトル<br>PGroonga：全文検索")]
        OPENAI["OpenAI API<br>text-embedding-3-small：埋め込み<br>gpt-4o-mini：紹介文・相談メッセージ"]
    end

    BATCH["初期ロード　initial_load/<br>マスタ・文書・章の埋め込み<br>キャリアシート・看板"]
    DEV["開発用の確認画面　_test/<br>rank/app.py：比重を動かす<br>search_eval/：評価セット11問"]

    USER --> CLOUD --> APP
    P1 --> S1
    P2 --> S2
    P4 --> S2
    P5 & P6 --> S3
    P3 --> R1
    R1 -- "登録のあと、関係者の看板を作り直す" --> S2
    R1 -- "文書・章・著者・キーワード" --> SUPA
    R1 -- "章 → ベクトル" --> OPENAI
    S1 -- "章のベクトルを読む<br>全文検索の関数（RPC）" --> SUPA
    S1 -- "質問文 → ベクトル" --> OPENAI
    S2 -- "看板の読み書き" --> SUPA
    S2 -- "看板 → ベクトル<br>紹介文・メッセージを書く" --> OPENAI
    S3 -- "章のベクトル・看板・キーワード・組織" --> SUPA
    BATCH --> SUPA
    BATCH --> OPENAI
    DEV -.-> S1

    classDef reg fill:#E1F5EE,stroke:#0F6E56,color:#0F6E56
    classDef srch fill:#EEEDFE,stroke:#534AB7,color:#534AB7
    class P3,R1 reg
    class P1,P2,P4,P5,P6,S1,S2,S3 srch
```

## 2. 部品の一覧

| 部品 | 役割 | 担当 | 状態 |
|---|---|---|---|
| app.py | 入口。サイドバーで6つの画面を切り替える | ほりえもん | 稼働中 |
| 人を探す | 相談文から、似た経験を持つ人を「実績のある人」と「原石の人」の2つの枠で出す | ほりえもん | 稼働中 |
| 仲間を探す | 社員番号を入れると、看板が近い人（考え方や関心が似ている人）を出す | ほりえもん | 稼働中 |
| もっと調べる | 選んだ人の看板とキャリアシートから、AIが紹介文を書く。書いた事実に出典を付けさせ、プログラムで確かめる | ほりえもん | 稼働中 |
| 属人化マップ | 文書を内容の近さで地図にし、ほかの人の文書に似たものがない文書（替えがききにくい経験）と、それを多く持つ人、引き継ぎの候補を出す（themes.py） | ほりえもん | 稼働中 |
| 空白地帯マップ | 文書を80のテーマに分け、困りごと・提案は多いのに解決（採択・完了）が少ないテーマと、その課題を出した人を出す（themes.py） | ほりえもん | 稼働中 |
| つながりマップ | 選んだ人の周りの、つながりの強い人を図にする。看板・テーマ・文章・組織の近さで強さを決め、関心は近いのに文書では接点が薄い人を「意外なつながり」として出す（network.py、pyvis） | ほりえもん | 稼働中 |
| 相談メッセージの下書き | もっと調べるの紹介文（出典つき）と相談したいことから、最初の依頼文をAIが書く。材料にない文書番号を含む文は捨てる（outreach.py） | ほりえもん | 稼働中 |
| 個人看板を検索（開発用） | 社員番号から、その人の看板の中身を見る。本番では出さない | ほりえもん | 稼働中 |
| 登録（register/） | Wordをチェックして、文書・章・著者・キーワードをDBに登録し、章を埋め込む。そのあと関係者の看板を作り直し、検索のキャッシュを消す（すぐ検索に出る） | まよりん | 稼働中 |
| 検索（search/） | ベクトル検索と全文検索を RRF でまとめ、章 → 文書 → 人の順に点数を集める | ほりえもん | 稼働中 |
| 看板（profiles・profile_notes） | 人ごとに、文書のノートとキャリアシートを並べた文章と、その埋め込み。画面には出さず、仲間を探す・もっと調べるに使う | ほりえもん | 稼働中 |
| Supabase | データベース（PostgreSQL）。pgvector で埋め込みを、PGroonga で全文検索の索引を持つ | － | 稼働中 |
| OpenAI API | 章・質問文・看板をベクトルにする（text-embedding-3-small、1536次元）。紹介文を書く（gpt-4o-mini） | － | 稼働中 |
| SudachiPy | 日本語を単語に分ける。登録のとき（全文検索の列・キーワード）と検索のとき（質問文）に使う | ほりえもん | 稼働中 |
| 初期ロード（initial_load/） | マスタ・文書（db_load_673.json と追加分 db_load_add_*.json）・章の埋め込み・キャリアシート・看板を入れる | ほりえもん・まよりん | 実行済み |
| 開発用の確認画面（_test/rank/app.py） | 全文の比重・役割の重みなどを動かし、順位と評価セット11問の点数がどう変わるかを見る。既定値を決めるための画面 | ほりえもん | 稼働中 |
| Streamlit Community Cloud | 公開先。GitHub の main から起動する。必要なパッケージは requirements.txt に書く | ほりえもん | 稼働中 |
| .env／Secrets | 接続情報とAPIキー（SUPABASE_URL・SUPABASE_KEY・OPENAI_API_KEY）。手元では `.env`（`.gitignore` 済みでGitには上げない）、公開先では Streamlit Cloud の Secrets に置く | 各自 | － |

## 3. DBの表（2026-10-10 時点の件数。ナレッジの拡充のあと）

| まとまり | 表 | 件数 | 何が入っているか |
|---|---|---|---|
| マスタ | departments | 41 | 部署 |
| マスタ | employees | 772 | 社員 |
| 事実データ | documents | 1,483 | 文書（改善提案 593／PJ文書 300／アイデア投稿 590） |
| 事実データ | document_sections | 6,335 | 章の本文・全文検索の列（body_tokens）・埋め込み。検索の単位 |
| 事実データ | document_authors | 2,023 | 誰がどの役割で文書に関わったか |
| 事実データ | document_keywords | 11,869 | 文書のキーワード（検索結果に出す） |
| AI生成物 | profiles | 767 | 看板の文章と埋め込み（1人1行） |
| AI生成物 | profile_notes | 3,014 | 看板のノート（文書の抜粋 1,775／キャリアシート 1,239・367人分） |
| 予定（未使用） | tags・cluster_runs・clusters・cluster_members・app_users・interests | 0 | タグ、クラスタ分析、ログイン、関心の記録 |

## 4. 未定のこと

- 公開URLでは、誰でも登録タブから文書を登録・上書きできる。発表のあとに公開を止めるか、登録タブを守るかを決める
- 登録タブでアイデア投稿（IDEA-）を扱うか。今は初期ロードでだけ入れている
- キャリアシートを画面から登録・更新するか。今はモックのJSONを初期ロードで入れている
- 予定の表（タグ・クラスタ・ログイン・関心の記録）を、発表までに使うか
