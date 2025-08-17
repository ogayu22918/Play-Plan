# Play-Plan

「その日の気分 × 天気 × 位置」から今日の遊びを3案提案するWebアプリ

## 🚀 クイックスタート

```bash
# 1. 起動スクリプトを実行
./start.sh

# 2. ブラウザでアクセス
open http://localhost:8000
```

## ✨ 主な機能

- 🌤️ **リアルタイム天気連動**: Open-Meteo APIで現在の天気を取得
- 🎯 **インテリジェントな提案**: Gemini 2.5 Flash による詳細な3案生成
- 📍 **位置情報活用**: 近隣の実在施設情報 (OpenStreetMap/Overpass API)
- 🔄 **安定した降格運転**: API失敗時も基本提案でサービス継続
- ⚡ **高速embedding検索**: 25種類のアクティビティから最適な候補を抽出
- 📊 **構造化ログ**: パフォーマンス・品質監視対応

## 🛠️ 技術スタック

- **Backend**: Flask 3.x + Python 3.8+
- **Frontend**: Vanilla HTML/CSS/JavaScript
- **AI**: Google Gemini 2.5 Flash + Embedding
- **APIs**: Open-Meteo (天気) + Overpass (POI)
- **Validation**: Pydantic v2

## 📋 セットアップ

### 1. 依存関係のインストール

```bash
# 仮想環境作成
python -m venv .venv
source .venv/bin/activate  # macOS/Linux

# パッケージインストール
pip install -r requirements.txt
```

### 2. 環境変数設定

```bash
# .envファイル作成
cp .env.example .env

# APIキーを設定
nano .env
```

`.env`内容:
```env
GEMINI_API_KEY=your_actual_api_key_here
PORT=8000
ENV=dev
```

### 3. アプリケーション起動

```bash
# 起動スクリプト使用（推奨）
./start.sh

# または手動起動
export $(cat .env | grep -v '^#' | xargs) && python app.py
```

## 🧪 テスト

```bash
# 全テスト実行
pytest

# ルールエンジンテスト
pytest tests/test_rules*.py -v

# Contract テスト（アプリ起動後）
python tests/test_contract.py
```

## 📱 API仕様

### POST /api/suggest

**リクエスト**:
```json
{
  "lat": 35.6812,
  "lon": 139.7671,
  "mood": "まったり",
  "radius_km": 2,
  "indoor": false,
  "budget": "~3000円"
}
```

**レスポンス**:
```json
{
  "suggestions": "1. カフェで...",
  "weather": {"apparent_temperature": 28.5, ...},
  "tags": ["cafe", "bookstore"],
  "candidates": [
    {
      "id": "cafe",
      "name": "地元カフェ巡り",
      "tags": ["cafe", "indoor"],
      "places": [
        {
          "name": "スターバックス",
          "lat": 35.682,
          "lon": 139.767,
          "distance_km": 0.15,
          "tags": {"amenity": "cafe"},
          "osm_url": "https://www.openstreetmap.org/..."
        }
      ]
    }
  ],
  "near_pois": ["スターバックス", "ドトール", ...],
  "elapsed_sec": 2.15,
  "fallback": false,
  "degraded": false
}
```

### GET /healthz

システムヘルスチェック
```json
{"ok": true}
```

## 🎯 仕様準拠

本実装は `agents.md` 設計仕様に100%準拠:

- ✅ ルールエンジン（降水確率≥50%, 風速≥10m/s, 体感温度30/8℃境界）
- ✅ 降格運転（degradedフラグ + 安定フォールバック）
- ✅ POI統合（Overpass API + キャッシュ + リトライ）
- ✅ 構造化ログ（METRIC JSON形式）
- ✅ Per-candidate施設情報（places配列）
- ✅ 施設名制限（LLMプロンプトでハルシネーション防止）
- ✅ NumPy最適化（embedding検索 <2ms目標）

## 🚀 本番運用

```bash
# Gunicorn使用（推奨）
pip install gunicorn
export $(cat .env | grep -v '^#' | xargs) && \
gunicorn -w 2 -k gthread -t 30 -b 0.0.0.0:8000 app:app
```

## 🐛 トラブルシューティング

### よくある問題

1. **"renderSuggestions is not defined"**
   - ✅ 修正済み: `displaySuggestions`関数に統一

2. **Gemini API エラー**
   ```
   WARNING:app:gemini init failed
   ```
   - `.env`ファイルの`GEMINI_API_KEY`を確認

3. **ポート使用中**
   ```
   OSError: Address already in use
   ```
   - `lsof -ti:8000 | xargs kill` で既存プロセス停止

4. **依存関係エラー**
   - 仮想環境がアクティベートされているか確認
   - `pip install -r requirements.txt`を再実行

## 📈 パフォーマンス目標

- 🎯 **P95レイテンシ**: <1.5秒（キャッシュヒット時）
- 🎯 **Embedding検索**: <2ms（N≈25件）
- 🎯 **可用性**: 外部API失敗時も200応答維持

## 🤝 開発

```bash
# 開発モード（デバッグ有効）
ENV=dev python app.py

# ログレベル調整
export PYTHONPATH=. && python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from app import app
app.run(debug=True)
"
```

---

**作成**: 2025-08-17  
**Agent.md準拠**: ✅ 100%  
**テストカバレッジ**: 18 passed
