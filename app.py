# app.py
import os, math, json
from flask import Flask, request, jsonify
import requests
from google import genai
from google.genai import types
import numpy as np
from pydantic import BaseModel, Field, ValidationError, conint, confloat, constr
from typing import Annotated, Optional

GEMINI_MODEL = "gemini-2.5-flash"  # 生成用
EMBEDDING_MODEL = "gemini-embedding-001"  # 検索用

app = Flask(__name__)
# Geminiクライアントは遅延初期化 (環境変数 GEMINI_API_KEY を明示使用)
client = None  # type: ignore


# ------------------------------------------------------------
# Pydantic スキーマ定義 (入力)
# ------------------------------------------------------------
class SuggestRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    mood: Optional[Annotated[str, constr(strip_whitespace=True, max_length=120)]] = None
    radius_km: Optional[int] = Field(default=None, gt=0, le=500, description="移動半径km")
    indoor: Optional[bool] = None
    budget: Optional[Annotated[str, constr(strip_whitespace=True, max_length=50)]] = None

    class Config:
        extra = "forbid"  # 未知フィールドはエラー


def _bad_request_from_validation(err: ValidationError):
    # エラー内容をフィールド + 短い理由に要約
    issues = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e.get("loc", []) if p != '__root__')
        issues.append(f"{loc}: {e.get('msg')}")
    return jsonify({"error": "invalid_request", "details": issues}), 400

def _bad_request(msg: str):
    return jsonify({"error": "invalid_request", "details": [msg]}), 400


@app.errorhandler(Exception)
def _unhandled(e: Exception):  # PIIを含む可能性のある request.data はログしない
    app.logger.exception("unhandled error: %s", e.__class__.__name__)
    return jsonify({"error": "internal_error"}), 500
# https://ai.google.dev/gemini-api/docs/quickstart

# 1) 起動時にアクティビティを読み込み＆埋め込みを用意（初回は計算して保存）
with open("activities_seed.json", "r", encoding="utf-8") as f:
    ACTIVITIES = json.load(f)

# 既存の埋め込みキャッシュがなければ作成
if os.path.exists("embeddings.npy"):
    EMB = np.load("embeddings.npy").astype(np.float32, copy=False)
else:
    texts = [f"{a['name']} {', '.join(a['tags'])}" for a in ACTIVITIES]
    res = client.models.embed_content(model=EMBEDDING_MODEL, contents=texts)  # ai.google.dev embeddings
    EMB = np.array([e.values for e in res.embeddings], dtype=np.float32)
    np.save("embeddings.npy", EMB)

# 行方向を単位ベクトル化（正確なコサイン類似度 = 内積でOK）
_emb_norms = np.linalg.norm(EMB, axis=1, keepdims=True) + 1e-9
EMB_UNIT = EMB / _emb_norms

def cosine_sim(a, b): return np.dot(a, b) / (np.linalg.norm(a)*np.linalg.norm(b)+1e-9)

def fetch_weather(lat, lon):
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           "&current=temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
           "&hourly=precipitation_probability&timezone=auto")
    return requests.get(url, timeout=6).json()  # Open-Meteo: APIキー不要
    # https://open-meteo.com/en/docs

def shortlist_by_rules(weather, user):
    # 仕様: 雨/降水確率/屋内希望/暑さ/寒さ/強風/気分キーワードに応じタグを付与(重複排除)
    c = weather.get("current", {}) if isinstance(weather, dict) else {}
    hourly = weather.get("hourly", {}) if isinstance(weather, dict) else {}
    precip = (c.get("precipitation") or 0) or 0
    app_temp = (c.get("apparent_temperature") or 0) or 0
    wind = (c.get("wind_speed_10m") or 0) or 0
    precip_prob = (hourly.get("precipitation_probability") or 0) or 0
    mood = user.get("mood", "") if isinstance(user, dict) else ""
    want_indoor = bool(user.get("indoor")) if isinstance(user, dict) else False

    tags = []
    indoor_trigger = precip > 0 or precip_prob >= 60 or want_indoor or wind >= 12
    if indoor_trigger:
        tags += ["indoor", "museum", "cinema", "boardgame", "spa", "arcade"]
    # 暑さ (>=30)
    if app_temp >= 30:
        tags += ["aquarium", "mall"]
    # 寒さ (<=5) -> 追加で spa (重複しても後で集合化)
    if app_temp <= 5:
        tags += ["spa"]
    # 冒険/まったり 気分
    if "冒険" in mood:
        tags += ["bouldering", "trampoline", "karaoke"]
    if "まったり" in mood:
        tags += ["cafe", "bookstore"]
    return list(set(tags))

def top_k_by_embedding(query_text: str, k: int = 12):
    """正確な上位K (コサイン類似) を ~O(n) で取得する最適化版。
    手順:
      1. 事前正規化済み EMB_UNIT と 正規化クエリの内積 = 類似度
      2. np.argpartition で上位Kインデックスを取得 (完全ソート回避)
      3. そのK件のみを降順ソート
    2000件程度では典型的に <2ms (M2) を目標。
    """
    if k <= 0:
        return []
    k = min(k, EMB_UNIT.shape[0])
    q_raw = client.models.embed_content(model=EMBEDDING_MODEL, contents=query_text).embeddings[0].values
    q = np.array(q_raw, dtype=np.float32, copy=False)
    q_norm = np.linalg.norm(q) + 1e-9
    q_unit = q / q_norm
    # 内積 (EMB_UNIT shape: [N,D]) @ (D,) -> (N,)
    sims = EMB_UNIT @ q_unit  # float32
    # 部分ソート: 上位K位置を抽出
    if k == EMB_UNIT.shape[0]:
        top_idx_unsorted = np.arange(k)
    else:
        top_idx_unsorted = np.argpartition(sims, -k)[-k:]
    # その範囲のみ降順並べ替え
    order = np.argsort(sims[top_idx_unsorted])[::-1]
    idx_sorted = top_idx_unsorted[order]
    return [ACTIVITIES[i] for i in idx_sorted]

@app.post("/api/suggest")
def suggest():
    """POST /api/suggest
    受信: {lat, lon, mood, radius_km, indoor, budget}
    1) Open-Meteo 現在天気 (10分メモリキャッシュ)
    2) shortlist_by_rules で候補タグ
    3) Gemini (gemini-2.5-flash, thinking無効) で3案生成
    4) JSON返却
    タイムアウト全体目標: 6秒
    エラー時: {"error": str} + 適切な4xx/5xx
    """
    import time, concurrent.futures, threading

    start = time.time()
    global client
    if client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"error": "GEMINI_API_KEY not set"}), 500
        try:
            from google import genai as _g
            # api_key を明示指定 (他の credential 方法と混同しない)
            globals()['client'] = client = _g.Client(api_key=api_key)
        except Exception as e:
            return jsonify({"error": f"gemini client init failed: {e}"}), 500
    BUDGET_SECONDS = 6.0

    # ---------- 入力バリデーション (Pydantic) ----------
    raw = request.get_json(silent=True)
    if raw is None:
        return _bad_request("JSON body required")
    try:
        # bool 文字列の正規化 (pydanticは true/false 文字列を解釈するが、空文字は None に変換)
        if isinstance(raw, dict) and "indoor" in raw and raw["indoor"] == "":
            raw["indoor"] = None
        req_model = SuggestRequest.model_validate(raw)
    except ValidationError as ve:
        return _bad_request_from_validation(ve)
    # PIIを含む mood/budget をログしないのでフィールド名のみ (DEBUG 用)
    app.logger.debug("validated fields: %s", list(req_model.model_dump(exclude_none=True).keys()))
    lat = req_model.lat
    lon = req_model.lon
    data = req_model.model_dump()

    # ---------- 天気キャッシュ (10分) ----------
    cache = globals().setdefault("_WEATHER_CACHE", {})  # {(lat_r,lon_r): (timestamp, weather_json)}
    lock = globals().setdefault("_WEATHER_CACHE_LOCK", threading.Lock())
    key = (round(lat, 2), round(lon, 2))
    weather = None
    now = time.time()
    with lock:
        if key in cache:
            ts, w = cache[key]
            if now - ts < 600:  # 10分
                weather = w
    if weather is None:
        # 残り時間チェック
        if time.time() - start >= BUDGET_SECONDS:
            return jsonify({"error": "timeout fetching weather"}), 504
        try:
            weather = fetch_weather(lat, lon)
        except requests.RequestException as e:
            return jsonify({"error": f"weather fetch failed: {e}"}), 502
        if not isinstance(weather, dict) or "current" not in weather:
            return jsonify({"error": "weather response invalid"}), 502
        with lock:
            cache[key] = (time.time(), weather)

    # ---------- ルールタグ生成 ----------
    try:
        rule_tags = shortlist_by_rules(weather, data) or []
    except Exception as e:
        return jsonify({"error": f"rule engine error: {e}"}), 500

    # ---------- Embedding検索候補 ----------
    if time.time() - start >= BUDGET_SECONDS:
        return jsonify({"error": "timeout before embedding"}), 504
    query = f"気分:{data.get('mood','')} タグ:{','.join(rule_tags)} 予算:{data.get('budget','未指定')}"
    try:
        candidates = top_k_by_embedding(query, k=8)
    except Exception as e:
        return jsonify({"error": f"embedding search failed: {e}"}), 500

    # ---------- Gemini 生成 ----------
    remaining = BUDGET_SECONDS - (time.time() - start)
    if remaining <= 0:
        return jsonify({"error": "timeout before generation"}), 504

    prompt = f"""
あなたは当日のレジャーコンシェルジュです。以下の条件で、実行可能性が高く多様性のある3案を日本語で提案してください。各案は:
1. タイトル（〜なプラン）
2. ひとことで魅力
3. 所要時間目安
4. 予算感（入力の予算があれば整合 / 無ければレンジ）
5. 天候(雨/暑さ/風) への配慮
6. 混雑/満席時の代替ミニプラン
を簡潔 (1項目あたり最大40文字程度) に列挙。Markdownの番号付きリストで。冗長な前置き不要。

[ユーザー条件]
気分: {data.get('mood')}
移動半径: {data.get('radius_km','未指定')}km
屋内希望: {data.get('indoor')}
予算: {data.get('budget','未指定')}
[現在の天気要約]
{json.dumps(weather.get('current', {}), ensure_ascii=False)}
[候補アクティビティ上位]
{json.dumps(candidates, ensure_ascii=False)}
""".strip()

    def _gen():
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            ),
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_gen)
            resp = fut.result(timeout=remaining)
    except concurrent.futures.TimeoutError:
        return jsonify({"error": "generation_timeout"}), 504
    except Exception as e:
        app.logger.warning("generation failure: %s", e.__class__.__name__)
        return jsonify({"error": "generation_failed"}), 502

    suggestions_text = getattr(resp, "text", None) or "".strip()
    if not suggestions_text:
        return jsonify({"error": "empty_generation"}), 502

    elapsed = round(time.time() - start, 3)
    return jsonify({
        "suggestions": suggestions_text,
        "weather": weather.get("current", {}),
        "tags": rule_tags,
        "candidates": candidates,
        "elapsed_sec": elapsed,
    })

if __name__ == "__main__":
    # 環境変数 PORT があれば利用
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)