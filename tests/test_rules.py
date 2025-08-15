import os, json
import numpy as np
import pytest

# --- 前処理: app.py インポート前に最低限のファイルを用意し副作用を抑える ---
if not os.path.exists("activities_seed.json"):
    with open("activities_seed.json", "w", encoding="utf-8") as f:
        json.dump([
            {"name": "Dummy Activity", "tags": ["sample"]},
            {"name": "Another", "tags": ["sample2"]},
        ], f, ensure_ascii=False)
if not os.path.exists("embeddings.npy"):
    np.save("embeddings.npy", np.zeros((2, 4), dtype=np.float32))

from app import shortlist_by_rules  # noqa: E402

INDOOR_TAGS = {"indoor", "museum", "cinema", "boardgame", "spa", "arcade"}
HOT_TAGS = {"aquarium", "mall"}
ADV_TAGS = {"bouldering", "trampoline", "karaoke"}
RELAX_TAGS = {"cafe", "bookstore"}


@pytest.mark.parametrize(
    "desc,weather,user,expected,raises",
    [
        (
            "ベース: 条件なし→空集合",
            {"current": {"precipitation": 0, "apparent_temperature": 20}},
            {"indoor": False, "mood": ""},
            set(),
            None,
        ),
        (
            "雨で屋内タグ付与 (降水1→雨扱い)",
            {"current": {"precipitation": 1, "apparent_temperature": 20}},
            {"indoor": False, "mood": ""},
            INDOOR_TAGS,
            None,
        ),
        (
            "降水0は雨扱いされない",
            {"current": {"precipitation": 0, "apparent_temperature": 20}},
            {"indoor": False, "mood": ""},
            set(),
            None,
        ),
        (
            "ユーザ屋内希望で屋内タグ (降水0)",
            {"current": {"precipitation": 0, "apparent_temperature": 25}},
            {"indoor": True, "mood": ""},
            INDOOR_TAGS,
            None,
        ),
        (
            "体感気温 30℃ ぴったりでHOTタグ (境界)",
            {"current": {"precipitation": 0, "apparent_temperature": 30}},
            {"indoor": False, "mood": ""},
            HOT_TAGS,
            None,
        ),
        (
            "体感気温 29.9℃ ではHOTタグ付かない",
            {"current": {"precipitation": 0, "apparent_temperature": 29.9}},
            {"indoor": False, "mood": ""},
            set(),
            None,
        ),
        (
            "冒険気分タグ",
            {"current": {"precipitation": 0, "apparent_temperature": 25}},
            {"indoor": False, "mood": "今日は冒険したい"},
            ADV_TAGS,
            None,
        ),
        (
            "まったり気分タグ",
            {"current": {"precipitation": 0, "apparent_temperature": 25}},
            {"indoor": False, "mood": "まったり気分"},
            RELAX_TAGS,
            None,
        ),
        (
            "雨 + 冒険気分 → 屋内 + 冒険タグの和集合",
            {"current": {"precipitation": 2, "apparent_temperature": 22}},
            {"indoor": False, "mood": "冒険"},
            INDOOR_TAGS | ADV_TAGS,
            None,
        ),
        (
            "強風値のみ (未使用要素) → 影響なし",
            {"current": {"precipitation": 0, "apparent_temperature": 22, "wind_speed_10m": 25}},
            {"indoor": False, "mood": ""},
            set(),
            None,
        ),
        (
            "降水Noneは0扱い",
            {"current": {"precipitation": None, "apparent_temperature": 22}},
            {"indoor": False, "mood": ""},
            set(),
            None,
        ),
        (
            "current欠如でも mood で取得",
            {},
            {"indoor": False, "mood": "まったり"},
            RELAX_TAGS,
            None,
        ),
        (
            "weatherが不正型で例外 (異常系)",
            "invalid",  # .getを持たない
            {"indoor": False, "mood": ""},
            None,
            Exception,
        ),
    ],
)
def test_shortlist_by_rules(desc, weather, user, expected, raises):
    if raises:
        with pytest.raises(raises):
            shortlist_by_rules(weather, user)
    else:
        result = set(shortlist_by_rules(weather, user))
        assert result == expected, f"{desc}: expected={expected} got={result}"
