# Play-Plan Agent 設計書 (Agent.md)

**更新日**: 2025-08-17
**対象バージョン**: MVP v0.1（Flask + HTML/CSS/JS）
**目的**: 「その日の気分 × 天気 × 位置」から“今日の遊び”を 3 案提案する Web アプリの設計指針。

---

## 1. TL;DR（要約）

* **入力**: 気分・所要時間・予算・屋内/屋外・移動半径・緯度経度
* **処理**: 天気取得 → ルールでタグ絞り込み → Embeddings 類似検索 → LLM で提案文整形
* **出力**: 実行可能性の高い順の 3 案（キャッチ、所要、予算、天候対策、代替案）
* **非機能**: 無料枠配慮（API 呼び出し最小化/キャッシュ）、セキュリティ（位置情報/鍵管理）、テスト容易性

---

## 2. スコープ / 非スコープ

### スコープ（MVP）

* フロントは **素の HTML/CSS/JS**（1ページ）
* バックエンドは **Flask**（単一サービス）
* 天気は **Open-Meteo**（API キー不要）
* LLM は **Gemini 2.5 Flash**（無料枠前提、thinking 0）
* 検索は **Gemini Embeddings** を用いた類似検索（ローカル前処理＆永続化）
* 提案は 3 案（テキスト整形のみ。リンクは汎用検索リンク程度）

### 非スコープ（MVP 以降）

* 予約/決済連携、リアルタイム混雑や料金の正確性保証
* 高精度の POI 検索（当面はプリセット + 任意で Overpass の軽量検索）
* 推薦の学習（ユーザー履歴の協調フィルタリング等）

---

## 3. アーキテクチャ

```
[Browser]
  └─ GET / (HTML/JS/CSS) ───────────────▶ [Flask]
                                         ├─ WeatherProvider(Open-Meteo)
                                         ├─ Rule Engine (weather × mood)
                                         ├─ Embedding Store (numpy, *.npy)
                                         └─ LLM(Gemini 2.5 Flash)
```

### ディレクトリ（推奨）

```
/ (repo root)
  app.py                  # Flask Entrypoint（/api/suggest, /healthz）
  activities_seed.json    # アクティビティ辞書（プリセット）
  embeddings.npy          # 上記をベクトル化して保存（初回生成）
  /public                 # 静的ファイル（index.html, app.js, styles.css）
  /templates              # 必要なら Jinja2 用
  /services               # weather.py, suggest.py（責務分割）
  /tests                  # pytest
  requirements.txt        # 依存定義
  .env.example            # GEMINI_API_KEY など
```

---

## 4. ユースケース / ユーザーストーリー

* **U1**: ユーザーは「まったり」「〜3000円」「徒歩 2km」「屋内」などを選び、現在地を許可→3 案表示。
* **U2**: 位置許可を拒否 → 住所/駅名を入力 → 近辺の天気で提案。
* **U3**: 天気 API が落ちている → ルールのみで簡易 3 案を返す（降格運転）。

---

## 5. 外部サービス

* **Open-Meteo**: 現在天気・降水・体感温度（キー不要）
* **Gemini API**: 生成（`gemini-2.5-flash`）
* **Gemini Embeddings**: 検索（`gemini-embedding-001`）
* **（任意）Overpass API**: 周辺 POI（軽量クエリ + キャッシュ前提）

---

## 6. データモデル

### 6.1 activities\_seed.json（抜粋スキーマ）

```json
{
  "id": "cafe",
  "name": "カフェでゆっくり",
  "tags": ["indoor", "cafe", "relax"],
  "weather_bias": {"rain": 0.7, "hot": 0.6, "cold": 0.8},
  "mood_bias": {"まったり": 0.9, "冒険": 0.2},
  "avg_cost": 1200,
  "typical_duration_min": 90,
  "note": "長居可、電源席は混雑注意"
}
```

### 6.2 Embeddings ストア

* `embeddings.npy`: `float32` の 2D 行列（`N × D`）
* ベクトル化テキスト: `name + tags + note`
* 正規化: 事前に L2 正規化し、内積＝コサイン類似度として扱う

### 6.3 キャッシュ

* 天気: `key=(grid:lat_lon_rounded_0.01)` に 10 分 TTL のメモリキャッシュ
* Overpass: `places_cache.sqlite`（name, lat, lon, category, fetched\_at）

---

## 7. API 仕様

### 7.1 POST /api/suggest

**Request (JSON)**

```json
{
  "lat": 35.6812,
  "lon": 139.7671,
  "mood": "まったり",
  "radius_km": 3,
  "indoor": false,
  "budget": "~3000円"
}
```

**Response (200)**

```json
{
  "suggestions": "…LLM生成テキスト…",
  "weather": { "current": {"apparent_temperature": 31.2, "precipitation": 0.0, …}},
  "tags": ["indoor", "cafe", "museum"],
  "degraded": false
}
```

**エラー**

* 400: バリデーションエラー（lat/lon 欠落など）
* 502: 外部 API タイムアウト（内部で降格に成功した場合は 200 + `degraded: true`）
* 500: 予期せぬエラー（LLM 失敗等）

### 7.2 GET /healthz

* 200: 起動 OK（依存の疎通はチェックしない軽量版）

---

## 8. 処理フロー（詳細）

1. **位置と入力**を受理 → スキーマバリデーション
2. **天気取得**（Open-Meteo、10 分キャッシュ）
3. **ルールでタグ絞り込み**

   * 降水 > 0 or 予報の降水確率 > 50% → `indoor` 優先
   * 体感温度 ≥ 30℃ → `aquarium`, `mall` を加点
   * 体感温度 ≤ 8℃ → `spa`, `sauna`, `cafe` を加点
   * 風速 ≥ 10m/s → 風の影響が少ない屋内系
   * 気分: 「冒険」→ `bouldering`, 「まったり」→ `cafe` 等
4. **Embedding 検索（Top-K）**

   * クエリ = `気分/タグ/予算` を連結した短文
   * コサイン類似度の上位 K=8 を候補に
5. **Gemini 生成**（`gemini-2.5-flash`, thinking 0）

   * 出力フォーマットを固定（3 案 + 要素ラベル）
6. **レスポンス整形**（degraded フラグ、fallback 文言）

---

## 9. ルールエンジン（MVP 初期ルール）

| 条件                   | 処理                                                             |
| -------------------- | -------------------------------------------------------------- |
| 降水 > 0 or 降水確率 ≥ 50% | `indoor`, `museum`, `cinema`, `boardgame`, `spa`, `arcade` を優先 |
| 体感温度 ≥ 30℃           | `aquarium`, `mall` を追加                                         |
| 体感温度 ≤ 8℃            | `sauna`, `cafe` を追加                                            |
| 風速 ≥ 10m/s           | 屋内系比率を強化                                                       |
| 気分=冒険                | `bouldering`, `trampoline`, `karaoke`                          |
| 気分=まったり              | `cafe`, `bookstore`                                            |

> ルールはシンプルに保ち、Embeddings に“語彙ゆらぎ”の吸収を任せる。

---

## 10. LLM プロンプト（テンプレ）

```
あなたは当日のレジャーコンシェルジュです。以下の条件で、実行可能性の高い順に3案、各案に:
- ひとことで魅力
- 所要時間目安
- 予算感
- 雨天/暑さ対策
- 代替プラン（混雑時）
を日本語で簡潔に。

[ユーザー]
気分: {{mood}}
移動半径: {{radius_km}} km
屋内希望: {{indoor}}
[現在の天気の要約]
{{current_weather_json}}
[候補アクティビティ（上位）]
{{candidates_json}}
```

### LLM 設定

* **モデル**: `gemini-2.5-flash`
* **thinking**: 0（速度/コスト優先）
* **最大出力**: 800–1200 文字程度
* **安全性**: 実在店舗の断定表現は避け、一般表現を推奨

---

## 11. 非機能（NFR）

* **性能**: P95 レイテンシ 1.5s 以内（キャッシュヒット時）
* **可用性**: 外部 API 失敗時の降格運転（ルールのみで応答）
* **コスト**: Embeddings の再計算は手動/起動時一括。生成は 1 リクエスト 1 回。

---

## 12. セキュリティ / プライバシー

* 位置情報は**明示許可**かつ **HTTPS** 前提。
* **GEMINI\_API\_KEY** はサーバー側環境変数に保存（フロントには出さない）。
* ログは市区レベルに丸め、精密座標は保存しない（デバッグ時のみ opt-in）。
* CORS: 同一オリジン想定。必要時のみ許可。
* XSS: DOM 挿入は `textContent`。

---

## 13. エラー処理 / 降格運転

* **分類**: 400（入力不正）/ 502（外部依存）/ 500（内部）
* **降格**: 天気・LLM 失敗時は、利用可能な情報のみで簡易 3 案を返す + `degraded: true`
* **再試行**: Open-Meteo は指数バックオフ（最大 3 回）

---

## 14. ロギング / モニタリング

* 構造化ログ（jsonlines）: `ts, path, latency_ms, degraded, mood, tags, weather_digest`
* メトリクス: 成功率、P50/P95、降格率、LLM エラー率

---

## 15. テスト戦略

* **Unit**: `shortlist_by_rules`（境界値: 体感 30℃, 降水 0/1, 風速 10m/s）
* **Contract**: `/api/suggest` リクエスト/レスポンススキーマ
* **Integration**: 天気 API のモック（タイムアウト/失敗）
* **Perf**: Top-K 抽出の 2ms 目標（N≈2k）

---

## 16. デプロイ / 運用

* **ローカル**: `flask run` or `python app.py`
* **本番**: `gunicorn -w 2 -k gthread -t 30 app:app`（Reverse Proxy の背後）
* **TLS**: リバースプロキシで終端、HSTS 有効化
* **環境変数**: `GEMINI_API_KEY`, `PORT`, `ENV=prod`

---

## 17. 依存関係（requirements.txt 例）

```
flask~=3.0
requests~=2.32
numpy~=1.26
google-genai~=0.2  # 新Gemini SDK
pydantic~=2.8
pytest~=8.3
```

---

## 18. 実装メモ / 擬似コード

```python
# 起動時: activities_seed.json をロード → 埋め込みキャッシュを確認
# ない場合は embed_content で一括計算 → embeddings.npy 保存

POST /api/suggest:
  req = validate(request.json)
  weather = weather_provider.get(req.lat, req.lon)  # 10分TTL
  tags = rule_engine.apply(weather, req)
  q = f"気分:{req.mood} タグ:{','.join(tags)} 予算:{req.budget}"
  candidates = embedding_store.topk(q, k=8)
  text = llm.generate(prompt(q, weather, candidates))
  return {"suggestions": text, "weather": weather, "tags": tags, "degraded": False}
except Timeout:
  return {"suggestions": fallback_from_rules(tags), "weather": None, "tags": tags, "degraded": True}
```

---

## 19. フロントエンド仕様（最小）

* **UI**: 気分（絵文字トグル）/ 所要 / 予算 / 屋内 / 半径 / 現在地ボタン
* **位置取得**: `navigator.geolocation`（拒否→住所入力にフォールバック）
* **表示**: 3 枚のカード（タイトル、要約、Tips、代替案）
* **失敗時**: トースト表示 + リトライボタン
* **セキュリティ**: 文字列挿入は `textContent`、リンクは `rel=noopener`

---

## 20. ロードマップ

* **v0.2**: Overpass API による近隣 POI の名称注入（キャッシュ必須）
* **v0.3**: ユーザー嗜好学習（お気に入り/既訪問の反映）
* **v0.4**: A/B テスト（プロンプト/ルールウェイト）
* **v0.5**: 多言語化（ja/en）

---

## 21. 既知の課題 / オープン質問

* Open-Meteo の体感温度・降水の粒度と、都市部の微気象のズレ
* 無料枠での LLM 安定性（レート/日次上限）
* embeddings.npy と seed JSON の整合（自動再生成の導線）

---

## 22. 付録

### 22.1 サンプル `curl`

```bash
curl -sS -X POST http://localhost:5000/api/suggest \
  -H 'Content-Type: application/json' \
  -d '{"lat":35.6812,"lon":139.7671,"mood":"まったり","radius_km":3,"indoor":false,"budget":"~3000円"}' | jq .
```

### 22.2 Definition of Done（MVP）

* `/api/suggest` が 200 を返し、3 案が生成される
* 天気失敗時も降格で応答（`degraded: true`）
* Unit/Contract テストが Green
* P95 < 1.5s（キャッシュヒット時）

---

*本書はリポジトリ直下に `Agent.md` として配置を想定。継続開発で差分が出たら本書を真実に合わせて更新すること（コードを真実としない）。*

## 23. 施設(POI)取得の追加仕様（Overpass 統合）

**目的**: ユーザーに具体的な施設名・座標・距離・地図リンクを表示し、提案の実行性を高める。まずは無料で賄う方針のため、MVP は Overpass (OpenStreetMap) を一次プロバイダーとする。

### 23.1 変更点概要

* `/api/suggest` のレスポンスに `candidates[].places` を追加する。
* `places` は各候補アクティビティに紐づく周辺 POI の配列（name, lat, lon, distance\_km, tags, osm\_url）。
* サーバー側で Overpass を呼び出し、取得結果をキャッシュして返す。
* LLM プロンプトでは「下記に列挙した施設のみを言及する」旨を厳密に指示し、ハルシネーション（事実でない施設情報の生成）を防ぐ。

### 23.2 API 変更（契約）

#### POST /api/suggest （既存）

**追加されたレスポンスフィールド**

```json
"candidates": [
  {
    "id": "cafe",
    "name": "カフェでゆっくり",
    "tags": ["indoor","cafe"],
    "places": [
      {"name":"喫茶ポケット","lat":35.68,"lon":139.76,"distance_km":0.15,"tags":{"amenity":"cafe"},"osm_url":"..."}
    ]
  }
]
```

* `places` が空の場合は `[]` を返す。
* POI 取得が失敗した場合は `degraded: true` を返し、既存のフォールバック提案を行う。

### 23.3 Overpass 呼び出し方針

* エンドポイント: `https://overpass-api.de/api/interpreter`（MVP）
* クエリ: `node/way/relation` の `around` 検索でカテゴリを絞る（例: `["amenity"="cafe"]`）
* レスポンスから `name`, `lat`, `lon`, `tags` を抽出。`lat/lon` が存在しない場合は `center` を参照。
* キャッシュ: メモリ TTL=600秒（10分）。本番は Redis 等を推奨。
* レート/負荷対策: UA ヘッダを設定、指数バックオフ（最大3回）、クエリ頻度を制限。大量ユーザー時はプロバイダー切替を検討。

### 23.4 Activity → OSM マッピング

* `ACTIVITY_TO_OSM` マップを用意し、候補の `id` や `tags` から OSM の key/value を決定する。
* 初期マッピング（例）:

  * `cafe` -> `("amenity", "cafe")`
  * `museum` -> `("tourism", "museum")`
  * `aquarium` -> `("tourism", "aquarium")`
  * `cinema` -> `("amenity", "cinema")`
  * `park` -> `("leisure", "park")`
  * `bouldering` -> `("sport", "climbing")`
* 不明時はフォールバックとして `("amenity","cafe")` 等の一般カテゴリを使う。

### 23.5 LLM プロンプトの強化（事実性担保）

* プロンプト冒頭で必ず次を明記する: 「以下に記載された施設のみを名前として言及し、それ以外の施設情報を生成するな。営業時間等はAPIにない場合は『要確認』と表示すること。」
* 施設リストを JSON としてプロンプトに埋め込み、LLM が参照すべき情報を明確に与える。

### 23.6 レスポンス想定（例）

* 既存の `suggestions`（LLMテキスト）に加え、`candidates[].places` を添えて返す。
* フロントは `places` をカード下にリスト表示し、`osm_url` を地図リンクとして提供する。

### 23.7 UI 変更点（最小）

* 結果領域に `places` 用の節を追加（店舗名 + 距離 + 地図リンク）。
* `degraded: true` の場合は上部に注意バナーを表示。
* 地図タイル表示は後回し。まずは `osm_url` リンクで対応。

### 23.8 テスト

* Unit: `fetch_pois_overpass` をモック化し、正常系/タイムアウト/空配列をカバー。
* Contract: `/api/suggest` のスキーマに `candidates[].places` が含まれることを検証する。

### 23.9 監視 / ロギング

* POI 呼び出し成功率、平均取得時間、キャッシュヒット率をメトリクスとして収集。
* 例外は `degraded=true` を付与して返し、詳細はサーバログに残す（ただし精密座標は丸める）。

### 23.10 将来の改善（設計メモ）

* Provider 抽象化レイヤを作り、`OverpassProvider` と `GooglePlacesProvider` を切替可能にする。
* Google Places を導入する場合は、NearBy Search -> Place Details のフローを組み、営業時間/評価/写真を表示可能にする（有料）。
* 大量ユーザーや高可用性が必要になったら、Overpass の利用を縮小して商用プロバイダーへ移行。

---

*上記の追加を Agent.md に追記しました。必要であればこのセクションを先頭に表示するサマリ版や、リポジトリに適用する `git diff`（patch）を作ります。どちらを先に用意しましょうか？*
