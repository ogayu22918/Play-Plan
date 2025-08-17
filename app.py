# app.py
import os, math, json, time
from flask import Flask, request, jsonify, send_from_directory
import requests
from google import genai
from google.genai import types
import numpy as np
from pydantic import BaseModel, Field, ValidationError, conint, confloat, constr, ConfigDict
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
    model_config = ConfigDict(extra="forbid")
    
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    mood: Optional[Annotated[str, constr(strip_whitespace=True, max_length=120)]] = None
    radius_km: Optional[int] = Field(default=None, gt=0, le=500, description="移動半径km")
    indoor: Optional[bool] = None
    budget: Optional[Annotated[str, constr(strip_whitespace=True, max_length=50)]] = None


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
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        # 通常のHTTPエラーはそのまま (404 等)
        return e
    # 詳細なエラーログを出力（デバッグ用）
    import traceback
    app.logger.error("UNHANDLED EXCEPTION:")
    app.logger.error("Type: %s", type(e).__name__)
    app.logger.error("Message: %s", str(e))
    app.logger.error("Traceback:\n%s", traceback.format_exc())
    app.logger.error("Request URL: %s", request.url if request else 'N/A')
    app.logger.error("Request method: %s", request.method if request else 'N/A')
    return jsonify({"error": "internal_error", "debug": str(e)}), 500
# https://ai.google.dev/gemini-api/docs/quickstart

# 1) 起動時にアクティビティを読み込み＆埋め込みを用意（初回は計算して保存）
with open("activities_seed.json", "r", encoding="utf-8") as f:
    ACTIVITIES = json.load(f)

# 既存の埋め込みキャッシュがなければ作成
if os.path.exists("embeddings.npy"):
    EMB = np.load("embeddings.npy").astype(np.float32, copy=False)
    _emb_norms = np.linalg.norm(EMB, axis=1, keepdims=True) + 1e-9
    EMB_UNIT = EMB / _emb_norms
else:
    # 初回起動時に Gemini API 利用不可 (キー未設定等) でもアプリを起動させたいので遅延生成
    EMB = None  # type: ignore
    EMB_UNIT = None  # type: ignore

def cosine_sim(a, b): return np.dot(a, b) / (np.linalg.norm(a)*np.linalg.norm(b)+1e-9)

def fetch_weather(lat, lon):
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           "&current=temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
           "&hourly=precipitation_probability&timezone=auto")
    return requests.get(url, timeout=6).json()  # Open-Meteo: APIキー不要
    # https://open-meteo.com/en/docs

def shortlist_by_rules(weather, user):
    """天気 + ユーザー気分から候補タグ集合を生成。
    Agent仕様 (agents.md) のルールに揃える:
      - 降水 >0 または 降水確率>=50% または indoor希望 または 風速>=10 で屋内系
      - 体感温度 >=30 で 暑さ回避タグ (aquarium, mall)
      - 体感温度 <=8 で 寒さタグ (sauna, cafe) *spa は従来互換で残す
      - 気分: 冒険→ bouldering/trampoline/karaoke, まったり→ cafe/bookstore
    """
    if not isinstance(weather, dict):
        raise TypeError("weather must be dict")
    c = weather.get("current", {}) or {}
    hourly = weather.get("hourly", {}) or {}
    def _first(v):
        if isinstance(v, list):
            return v[0] if v else 0
        return v
    def _f(x):
        try:
            return float(x)
        except Exception:
            return 0.0
    precip = _f(_first(c.get("precipitation")))
    app_temp = _f(_first(c.get("apparent_temperature")))
    wind = _f(_first(c.get("wind_speed_10m")))
    pp_raw = hourly.get("precipitation_probability")
    if isinstance(pp_raw, list) and pp_raw:
        precip_prob = _f(pp_raw[0])
    else:
        precip_prob = _f(pp_raw)
    mood = (user.get("mood") if isinstance(user, dict) else "") or ""
    want_indoor = bool(user.get("indoor")) if isinstance(user, dict) else False

    tags = []
    if (precip > 0) or (precip_prob >= 50) or want_indoor or (wind >= 10):
        tags += ["indoor", "museum", "cinema", "boardgame", "spa", "arcade"]
    if app_temp >= 30:
        tags += ["aquarium", "mall"]
    if app_temp <= 8:
        tags += ["sauna", "cafe", "spa"]  # spa 互換
    if "冒険" in mood:
        tags += ["bouldering", "trampoline", "karaoke"]
    if "まったり" in mood:
        tags += ["cafe", "bookstore"]
    return list(dict.fromkeys(tags))  # 順序保持 & 重複除去

def _ensure_embeddings():
    """埋め込み行列がまだ無ければ作成。失敗時は False を返す。"""
    global EMB, EMB_UNIT, client
    if EMB is not None and EMB_UNIT is not None:
        return True
    if client is None:
        return False
    try:
        texts = [f"{a['name']} {', '.join(a['tags'])}" for a in ACTIVITIES]
        embeddings = []
        for text in texts:
            res = client.embed_content(model=EMBEDDING_MODEL, content=text)
            embeddings.append(res['embedding'])
        EMB = np.array(embeddings, dtype=np.float32)
        np.save("embeddings.npy", EMB)
        _n = np.linalg.norm(EMB, axis=1, keepdims=True) + 1e-9
        EMB_UNIT = EMB / _n
        return True
    except Exception as e:  # ログのみ、フォールバックへ
        app.logger.warning("embed matrix init failed: %s", e.__class__.__name__)
        EMB = None
        EMB_UNIT = None
        return False

def top_k_by_embedding(query_text: str, k: int = 12):
    """正確な上位K (コサイン類似) を ~O(n) で取得する最適化版。
    手順:
      1. 事前正規化済み EMB_UNIT と 正規化クエリの内積 = 類似度
      2. np.argpartition で上位Kインデックスを取得 (完全ソート回避)
      3. そのK件のみを降順ソート
    2000件程度では典型的に <2ms (M2) を目標。
    """
    if k <= 0 or client is None:
        return []
    if not _ensure_embeddings():
        return []
    try:
        k = min(k, EMB_UNIT.shape[0])
        q_raw = client.embed_content(model=EMBEDDING_MODEL, content=query_text)['embedding']
        q = np.asarray(q_raw, dtype=np.float32)
        q_unit = q / (np.linalg.norm(q) + 1e-9)
        sims = EMB_UNIT @ q_unit
        top_idx_unsorted = np.arange(k) if k == EMB_UNIT.shape[0] else np.argpartition(sims, -k)[-k:]
        order = np.argsort(sims[top_idx_unsorted])[::-1]
        idx_sorted = top_idx_unsorted[order]
        return [ACTIVITIES[i] for i in idx_sorted]
    except Exception as e:
        app.logger.warning("embed query failed: %s %s", e.__class__.__name__, str(e))
        app.logger.debug("EMB_UNIT shape: %s, k: %s, query: %s", EMB_UNIT.shape if EMB_UNIT is not None else None, k, query_text[:50])
        return []

def _generate_fallback_suggestions(weather, user_data, rule_tags, candidates):
    """Gemini APIが利用できない場合のフォールバック提案生成"""
    current = weather.get("current", {})
    mood = user_data.get("mood", "").strip()
    indoor = user_data.get("indoor")
    budget = user_data.get("budget", "").strip()
    radius_km = user_data.get("radius_km")
    
    # 天気情報の解析
    temp = current.get("apparent_temperature", 20)
    precip = current.get("precipitation", 0)
    
    # 基本的な提案を3つ生成
    suggestions = []
    
    # 提案1: 天気と気分に基づく基本提案
    if "まったり" in mood or "のんびり" in mood or "リラックス" in mood:
        if indoor or precip > 0:
            title = "室内でまったりプラン"
            activities = ["カフェでコーヒータイム", "本屋で読書", "美術館・博物館巡り"]
        else:
            title = "屋外でまったりプラン" 
            activities = ["公園でピクニック", "散歩コース探索", "オープンテラスカフェ"]
    elif "冒険" in mood or "アクティブ" in mood or "運動" in mood:
        if indoor or precip > 0:
            title = "屋内アクティブプラン"
            activities = ["ボルダリング", "トランポリン", "カラオケ"]
        else:
            title = "アウトドア冒険プラン"
            activities = ["ハイキング", "サイクリング", "スポーツ施設"]
    else:
        if indoor or precip > 0:
            title = "室内エンジョイプラン"
            activities = ["ショッピングモール", "映画館", "アミューズメント施設"]
        else:
            title = "お出かけプラン"
            activities = ["観光スポット巡り", "地元グルメ探索", "季節のイベント"]
    
    # 天気に応じた調整
    weather_note = ""
    if precip > 0:
        weather_note = "（雨天のため屋内中心）"
    elif temp >= 30:
        weather_note = "（暑いため涼しい場所中心）"
        activities = [act.replace("屋外", "涼しい場所での") for act in activities]
    elif temp <= 10:
        weather_note = "（寒いため暖かい場所中心）"
        activities = [act.replace("屋外", "暖かい場所での") for act in activities]
    
    activity_text = "、".join(activities[:3])
    
    # 移動時間・予算の目安
    time_estimate = "2-4時間"
    if radius_km and radius_km <= 3:
        time_estimate = "1-3時間（近場中心）"
    elif radius_km and radius_km >= 10:
        time_estimate = "半日〜1日（広範囲）"
    
    budget_note = ""
    if budget:
        budget_note = f"\n予算目安: {budget}以内"
    elif "まったり" in mood:
        budget_note = "\n予算目安: 1000-3000円"
    else:
        budget_note = "\n予算目安: 2000-5000円"
    
    suggestion1 = f"""1. {title}{weather_note}
{activity_text}
所要時間: {time_estimate}{budget_note}
"""
    
    # 提案2: 候補アクティビティベース
    if candidates:
        cand_names = [c.get("name", "活動") for c in candidates[:3]]
        suggestion2 = f"""2. 人気スポット巡りプラン
{", ".join(cand_names)}
各スポット1-2時間ずつ楽しむ
所要時間: 3-5時間{budget_note}
"""
    else:
        # タグベースの提案
        tag_activities = {
            "cafe": "カフェホッピング",
            "museum": "文化施設巡り", 
            "cinema": "映画鑑賞",
            "mall": "ショッピング",
            "aquarium": "水族館",
            "spa": "スパ・温泉",
            "bookstore": "本屋巡り"
        }
        
        tag_based = []
        for tag in rule_tags[:3]:
            if tag in tag_activities:
                tag_based.append(tag_activities[tag])
        
        if tag_based:
            suggestion2 = f"""2. おすすめ活動プラン
{", ".join(tag_based)}
天気や気分にぴったりの活動
所要時間: 2-4時間{budget_note}
"""
        else:
            suggestion2 = f"""2. 定番お出かけプラン
地元の人気スポット巡り
カフェ、ショップ、観光地を組み合わせ
所要時間: 3-5時間{budget_note}
"""
    
    # 提案3: 時間帯・天気特化プラン
    import datetime
    hour = datetime.datetime.now().hour
    
    if hour < 12:
        time_plan = "朝活プラン"
        time_activities = "朝食カフェ → 散歩 → 午前中の空いているスポット"
    elif hour < 17:
        time_plan = "午後満喫プラン" 
        time_activities = "ランチ → メインアクティビティ → カフェタイム"
    else:
        time_plan = "夕方〜夜プラン"
        time_activities = "夕食 → ナイトスポット → 夜景スポット"
    
    suggestion3 = f"""3. {time_plan}
{time_activities}
時間帯を活かした効率的なルート
所要時間: 2-4時間{budget_note}
"""
    
    # 近隣POI (suggest 内で user_data['_near_pois'] として渡される想定)
    near_pois = user_data.get("_near_pois") or []
    if near_pois:
        suggestion4 = """4. 近場スポット候補
{spots}
半径内で見つかった場所（参考）""".format(spots=", ".join(near_pois[:6]))
        return suggestion1 + "\n" + suggestion2 + "\n" + suggestion3 + "\n" + suggestion4
    return suggestion1 + "\n" + suggestion2 + "\n" + suggestion3

# ---------------- 近隣POI取得 (OpenStreetMap Overpass) ----------------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TAG_TO_OSM_FEATURES = {
    "cafe": [("amenity", "cafe")],
    "bookstore": [("shop", "books")],
    "museum": [("tourism", "museum")],
    "aquarium": [("tourism", "aquarium")],
    "cinema": [("amenity", "cinema")],
    "spa": [("leisure", "spa"), ("amenity", "spa")],
    "sauna": [("leisure", "sauna"), ("amenity", "sauna")],
    "mall": [("shop", "mall"), ("amenity", "marketplace")],
    "boardgame": [("shop", "games")],
    "arcade": [("amenity", "arcade")],
    "bouldering": [("leisure", "climbing")],
    "trampoline": [("leisure", "trampoline")],
    "karaoke": [("amenity", "karaoke")],
    "park": [("leisure", "park")],
}

def fetch_nearby_pois(lat: float, lon: float, radius_m: int, rule_tags, remaining_budget: float):
    """Overpass API から近隣POI名 (最大8件) を取得。
    - radius_m は 200〜5000 にクリップ。
    - rule_tags から最大3カテゴリを抽出し複合クエリ。
    - 残り時間が不足 / 失敗時は空配列。
    - 10分キャッシュ。
    """
    import time
    if remaining_budget <= 0:
        return []
    radius_m = int(min(max(radius_m, 200), 5000))
    selected = []
    for t in rule_tags:
        if t in TAG_TO_OSM_FEATURES and t not in selected:
            selected.append(t)
        if len(selected) >= 3:
            break
    if not selected:
        return []
    key = (round(lat,3), round(lon,3), radius_m//1000, tuple(sorted(selected)))
    cache = globals().setdefault("_POI_CACHE", {})
    now = time.time()
    cached = cache.get(key)
    if cached and now - cached[0] < 600:
        return cached[1]
    parts = []
    for tag in selected:
        for k,v in TAG_TO_OSM_FEATURES[tag]:
            parts.append(f"node[\"{k}\"=\"{v}\"](around:{radius_m},{lat},{lon});")
    if not parts:
        return []
    query = "[out:json][timeout:8];(" + "".join(parts) + ");out qt 20;"
    if remaining_budget < 1.0:
        return []
    timeout = 2.0 if remaining_budget >= 2.5 else max(0.5, remaining_budget*0.6)
    try:
        import requests as _rq
        for attempt in range(3):
            try:
                resp = _rq.post(OVERPASS_URL, data={"data": query}, timeout=timeout, headers={"User-Agent": "PlayPlan/0.1 (+github)"})
                if resp.status_code != 200:
                    raise RuntimeError(f"status {resp.status_code}")
                js = resp.json()
                names = []
                for el in js.get("elements", []):
                    tg = el.get("tags") or {}
                    name = tg.get("name:ja") or tg.get("name")
                    if name and name not in names:
                        names.append(name)
                    if len(names) >= 8:
                        break
                cache[key] = (time.time(), names)
                return names
            except Exception:
                time.sleep(0.25 * (attempt + 1))
        return []
    except Exception as e:
        app.logger.debug("poi fetch failed: %s", e.__class__.__name__)
        return []

def augment_candidates_with_places(candidates, lat: float, lon: float, radius_m: int, time_budget: float):
    """candidates (list[dict]) に places 情報を付与。
    - 重いので 1 回の Overpass クエリ (max 3カテゴリ) にまとめ近傍POI を取得し分類。
    - 返却: 変更済 candidates
    - 失敗時は何もしない
    """
    start = time.time()
    if not candidates or time_budget <= 0:
        return candidates
    # 抽出したいタグ集合
    wanted = []
    for c in candidates:
        for t in c.get("tags", []):
            if t in TAG_TO_OSM_FEATURES and t not in wanted:
                wanted.append(t)
            if len(wanted) >= 3:
                break
        if len(wanted) >= 3:
            break
    if not wanted:
        return candidates
    radius_m = int(min(max(radius_m, 200), 4000))
    parts = []
    for tag in wanted:
        for k,v in TAG_TO_OSM_FEATURES[tag]:
            parts.append(f"node[\"{k}\"=\"{v}\"](around:{radius_m},{lat},{lon});")
    query = "[out:json][timeout:8];(" + "".join(parts) + ");out center qt 40;"
    key = (round(lat,3), round(lon,3), radius_m//100, tuple(sorted(wanted)))
    cache = globals().setdefault("_POI_DETAIL_CACHE", {})
    now = time.time()
    cached = cache.get(key)
    if cached and now - cached[0] < 600:
        elements = cached[1]
    else:
        try:
            import requests as _rq
            remain = time_budget - (time.time() - start)
            if remain <= 0:
                return candidates
            # retries
            last_err = None
            for attempt in range(3):
                try:
                    resp = _rq.post(OVERPASS_URL, data={"data": query}, timeout=min(2.0, max(0.5, remain)), headers={"User-Agent": "PlayPlan/0.1 (+github)"})
                    if resp.status_code != 200:
                        raise RuntimeError(f"status {resp.status_code}")
                    js = resp.json()
                    elements = js.get("elements", [])
                    cache[key] = (time.time(), elements)
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.25 * (attempt + 1))
            else:
                app.logger.debug("augment Overpass failed: %s", last_err)
                return candidates
        except Exception as e:
            app.logger.debug("augment request error: %s", e.__class__.__name__)
            return candidates
    # ユーティリティ: 距離
    def _dist_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(a))
    # 先に POI をタグ毎に分類
    bucket = {t: [] for t in wanted}
    for el in elements:
        tags = el.get("tags") or {}
        name = tags.get("name:ja") or tags.get("name")
        if not name:
            continue
        lat_p = el.get("lat") or (el.get("center") or {}).get("lat")
        lon_p = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat_p is None or lon_p is None:
            continue
        for t in wanted:
            for k,v in TAG_TO_OSM_FEATURES[t]:
                if tags.get(k) == v:
                    bucket[t].append({
                        "name": name,
                        "lat": lat_p,
                        "lon": lon_p,
                        "distance_km": round(_dist_km(lat, lon, lat_p, lon_p), 3),
                        "tags": {k: v},
                        "osm_url": f"https://www.openstreetmap.org/{el.get('type','node')}/{el.get('id')}"
                    })
                    break
            # マッチ1カテゴリのみ登録
            if bucket[t] and bucket[t][-1]["name"] == name:
                break
    # 各 candidate に places を紐付け (最大3件)
    for c in candidates:
        c_tags = c.get("tags", [])
        places = []
        for t in c_tags:
            if t in bucket and bucket[t]:
                for p in bucket[t]:
                    if len(places) >= 3:
                        break
                    if p["name"] not in {pl["name"] for pl in places}:
                        places.append(p)
            if len(places) >= 3:
                break
        c["places"] = places
        # id が無ければ簡易スラグ
        if "id" not in c:
            c["id"] = c.get("name", "").strip().replace(" ", "_")[:40]
    return candidates

# ------------------------------------------------------------
# フロントエンド配信: public/ 配下 (index.html + 静的資産)
# ルート / と任意の非APIパスを SPA 的に index.html へフォールバック
# ------------------------------------------------------------
@app.route('/public/<path:filename>')
def public_files(filename):
    return send_from_directory('public', filename)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def frontend(path: str):
    # /api/ で始まるものはここでは扱わない
    if path.startswith('api/'):
        return jsonify({"error": "not_found"}), 404
    # 直接ファイルが存在すれば返却
    full_path = os.path.join('public', path)
    if path and os.path.isfile(full_path):
        return send_from_directory('public', path)
    # 既定で index.html
    return send_from_directory('public', 'index.html')

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

    # 詳細ログ追加（デバッグ用）
    app.logger.info("=== /api/suggest REQUEST START ===")
    
    start = time.time()
    global client
    client_failed = False
    if client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                globals()['client'] = client = genai
            except Exception as e:
                app.logger.warning("gemini init failed: %s", e.__class__.__name__)
                client_failed = True
        else:
            client_failed = True
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
    degraded = False
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
            # 簡易リトライ (指数バックオフ 3回)
            last_err = None
            for attempt in range(3):
                try:
                    weather = fetch_weather(lat, lon)
                    break
                except requests.RequestException as e:
                    last_err = e
                    time.sleep(0.3 * (attempt + 1))
            else:
                degraded = True
                weather = {"current": {}, "_error": f"weather_failed:{last_err}"}
        except Exception as e:
            degraded = True
            weather = {"current": {}, "_error": f"weather_failed:{e.__class__.__name__}"}
        if not isinstance(weather, dict) or "current" not in weather:
            degraded = True
            weather = {"current": {}, "_error": "weather_invalid"}
        with lock:
            cache[key] = (time.time(), weather)

    # ---------- ルールタグ生成 ----------
    try:
        rule_tags = shortlist_by_rules(weather, data) or []
    except Exception as e:
        return jsonify({"error": f"rule engine error: {e}"}), 500

    # ---------- 近隣POI取得 (位置情報 + 半径利用) ----------
    near_pois = []
    if data.get("radius_km") and not os.environ.get("DISABLE_POI"):
        remaining_for_poi = BUDGET_SECONDS - (time.time() - start)
        try:
            near_pois = fetch_nearby_pois(
                lat, lon,
                radius_m=int(data["radius_km"] * 1000),
                rule_tags=rule_tags,
                remaining_budget=remaining_for_poi,
            ) or []
            if near_pois:
                data["_near_pois"] = near_pois
        except Exception:
            near_pois = []

    # ---------- Embedding検索候補 ----------
    if time.time() - start >= BUDGET_SECONDS:
        return jsonify({"error": "timeout before embedding"}), 504
    query = f"気分:{data.get('mood','')} タグ:{','.join(rule_tags)} 予算:{data.get('budget','未指定')}"
    candidates = []
    if not client_failed:
        # embeddings の長さと activities の整合性チェック (不一致なら再生成トライ)
        try:
            if EMB is not None and EMB.shape[0] != len(ACTIVITIES):
                app.logger.warning("embedding shape mismatch -> regenerating")
                _ensure_embeddings()
        except Exception:
            pass
        candidates = top_k_by_embedding(query, k=8) or []

    # ---------- Gemini 生成 ----------
    remaining = BUDGET_SECONDS - (time.time() - start)
    # 候補に施設情報付与 (embed後, LLM前)
    if remaining > 0 and candidates and data.get("radius_km") and not os.environ.get("DISABLE_POI"):
        try:
            augment_candidates_with_places(candidates, lat, lon, int(data.get("radius_km",1)*1000), remaining * 0.6)
        except Exception as e:
            app.logger.debug("augment failed: %s", e.__class__.__name__)
            degraded = True
    if remaining <= 0:
        # 生成を諦めフォールバック
        elapsed = round(time.time() - start, 3)
        fallback_suggestions = _generate_fallback_suggestions(weather, data, rule_tags, candidates)
        response_data = {
            "suggestions": fallback_suggestions,
            "weather": weather.get("current", {}),
            "tags": rule_tags,
            "candidates": candidates,
            "elapsed_sec": elapsed,
            "fallback": True,
            "fallback_reason": "timeout",
            "degraded": True,
        }
        app.logger.info("=== /api/suggest TIMEOUT FALLBACK ===")
        app.logger.info("Response status: 200")
        app.logger.info("Elapsed: %ss", elapsed)
        return jsonify(response_data)

    # 施設名抽出（後で places 拡張時に再利用予定）
    place_names = []
    for cdd in candidates:
        for pl in cdd.get('places', []) if isinstance(cdd, dict) else []:
            if pl.get('name') and pl['name'] not in place_names:
                place_names.append(pl['name'])
    allow_places = ', '.join(place_names[:20]) or '（該当施設データなし）'
    prompt = f"""
あなたは当日のレジャーコンシェルジュです。以下の条件で、実行可能性が高く多様性のある3案を日本語で提案してください。各案は:
1. タイトル（〜なプラン）
2. ひとことで魅力
3. 所要時間目安
4. 予算感（入力の予算があれば整合 / 無ければレンジ）
5. 天候(雨/暑さ/風) への配慮
6. 混雑/満席時の代替ミニプラン
を簡潔 (1項目あたり最大40文字程度) に列挙。Markdownの番号付きリストで。冗長な前置き不要。

注意: 以下のリストに含まれる施設名以外の固有名詞は作らないこと。存在しない店名や具体的な店舗の創作禁止。
利用可能な施設名: {allow_places}

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

    suggestions_text = None
    if not client_failed and client is not None:
        def _gen():
            model = client.GenerativeModel(GEMINI_MODEL)
            return model.generate_content(prompt)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_gen)
                resp = fut.result(timeout=remaining)
            suggestions_text = (getattr(resp, "text", None) or "").strip() or None
        except Exception as e:
            app.logger.warning("generation failed: %s", e.__class__.__name__)

    if not suggestions_text:
        # フォールバック: より実用的な提案を生成
        elapsed = round(time.time() - start, 3)
        fallback_suggestions = _generate_fallback_suggestions(weather, data, rule_tags, candidates)
        response_data = {
            "suggestions": fallback_suggestions,
            "weather": weather.get("current", {}),
            "tags": rule_tags,
            "candidates": candidates,
            "near_pois": near_pois,
            "elapsed_sec": elapsed,
            "fallback": True,
            "degraded": True,
        }
        app.logger.info("=== /api/suggest FALLBACK ===")
        app.logger.info("Response status: 200")
        app.logger.info("Elapsed: %ss", elapsed)
        return jsonify(response_data)

    elapsed = round(time.time() - start, 3)
    response_data = {
        "suggestions": suggestions_text,
        "weather": weather.get("current", {}),
        "tags": rule_tags,
        "candidates": candidates,
        "near_pois": near_pois,
        "elapsed_sec": elapsed,
        "fallback": False,
        "degraded": degraded,
    }
    app.logger.info("=== /api/suggest SUCCESS ===")
    app.logger.info("Response status: 200")
    app.logger.info("Elapsed: %ss", elapsed)
    # 構造化ログ 1行
    try:
        weather_digest = {}
        cw = weather.get("current", {}) if isinstance(weather, dict) else {}
        for k in ["apparent_temperature", "precipitation", "wind_speed_10m"]:
            if k in cw:
                weather_digest[k] = cw[k]
        log_obj = {
            "ts": time.time(),
            "path": "/api/suggest",
            "latency_ms": int(elapsed * 1000),
            "degraded": response_data.get("degraded"),
            "tags": rule_tags,
            "mood_present": bool(data.get("mood")),
            "radius_km": data.get("radius_km"),
            "poi_attached": any(c.get("places") for c in candidates),
            "weather": weather_digest,
        }
        app.logger.info("METRIC %s", json.dumps(log_obj, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass
    return jsonify(response_data)

@app.get('/healthz')
def healthz():
    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    # ログレベルを設定（デバッグ用）
    import logging
    logging.basicConfig(level=logging.INFO)
    app.logger.setLevel(logging.INFO)
    
    # 環境変数 PORT があれば利用
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)