# 検索と分析の検証（verify_analysis.py）

## 目的

モックデータ373件（提案書313件、PJ文書60件）を使って、次の2点が働くかを確かめます。

- **検索**：意味の近さで、正解の人を上位に出せるか。キーワード検索（BM25）と比べます。
- **分析B（車輪の再発明）**：同じ解決策を書いた文書どうしが、1つの塊になるか。

## ファイル

| ファイル | 内容 |
|---|---|
| verify_analysis.py | 検証の本体 |
| eval_queries.json | 評価セット（クエリ、正解の文書と人、繋がるべき組と繋がってはいけない組） |
| db_load_373.json | 入力データ（DB投入用と同じもの） |

3つを同じフォルダに置きます。

## 準備

```
pip install numpy pandas scikit-learn openai python-dotenv
```

### APIキーの設定（本番モデルで実行する場合）

同じフォルダに`.env`という名前のファイルを作り、次の1行を書きます。

```
OPENAI_API_KEY=sk-...
```

- コードは起動時に`.env`を自動で読み込みます（`python-dotenv`が必要です）。
- `.env`を使わず、環境変数`OPENAI_API_KEY`を直接設定しても動きます。
- `.env`にはAPIキーが入っているので、Gitにコミットしないでください。`.gitignore`に`.env`が入っているか確認してください。

## 実行

```
# 課金なしの試運転（文字の重なりで判断するので、数値は参考程度）
python verify_analysis.py --backend tfidf

# 本番と同じモデル（text-embedding-3-small）
python verify_analysis.py --backend openai
```

- 本番モデルの費用は1回数円程度です。埋め込みは`cache/`に保存され、2回目以降は課金されません。
- 閾値や方式を変えるときは、`--thresholds 0.60,0.65,0.70`や`--methods average,single`で指定します。

## 出力

画面に要約が表示され、明細は`reports/<backend>/`にCSVで保存されます。

| ファイル | 内容 |
|---|---|
| search_metrics.csv | クエリごとの検索の精度（正解の順位、Recall、MRR） |
| search_top10.csv | クエリごとの上位10人 |
| cluster_summary.csv | 方式と閾値ごとの塊の数、最大の塊、繋がるべき組が成立したか |
| cluster_members.csv | 塊ごとに、含まれる文書 |
| pair_sims.csv | 確認したい組の類似度 |

## 評価セットを変えるとき

`eval_queries.json`を編集します。クエリの文、正解の文書（`expected_docs`）、正解の人（`expected_emp`）、検索者（`searcher_emp`）を書き換えれば、コードを直さずに検証をやり直せます。
