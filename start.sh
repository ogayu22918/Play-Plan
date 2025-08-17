#!/bin/bash
# Play-Plan起動スクリプト

set -e

echo "🚀 Play-Plan を起動します..."

# ディレクトリ移動
cd "$(dirname "$0")"

# 仮想環境の確認
if [ ! -d ".venv" ]; then
    echo "仮想環境を作成中..."
    python -m venv .venv
fi

# 仮想環境のアクティベート
source .venv/bin/activate

# 依存関係のインストール
echo "依存関係をチェック中..."
pip install -q -r requirements.txt

# 環境変数の確認
if [ ! -f ".env" ]; then
    echo "⚠️  .envファイルが見つかりません。.env.exampleをコピーして編集してください。"
    cp .env.example .env
    echo "✏️  .envファイルを編集してGEMINI_API_KEYを設定してください。"
    exit 1
fi

# 環境変数を読み込み（改善版）
set -a
source .env
set +a

# API キーの確認
if [ -z "$GEMINI_API_KEY" ] || [ "$GEMINI_API_KEY" = "your_api_key_here" ]; then
    echo "❌ GEMINI_API_KEYが設定されていません。.envファイルを編集してください。"
    exit 1
fi

echo "✅ 環境設定OK"
echo "🌐 アプリケーションを起動中... (http://localhost:${PORT:-8000})"
echo "🛑 停止するには Ctrl+C を押してください"

# アプリケーション起動
python app.py
