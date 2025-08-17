import os, json, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
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
COLD_TAGS = {"sauna", "cafe", "spa"}


@pytest.mark.parametrize(
    "desc,weather,user,expected_subset,expect_empty,raises",
    [
        (
            "ベース: 条件なし→空集合",
            {"current": {"precipitation": 0, "apparent_temperature": 20}},
            {"indoor": False, "mood": ""},
            set(),
            True,
            None,
        ),
        (
            "雨で屋内タグ付与 (降水1)",
            {"current": {"precipitation": 1, "apparent_temperature": 20}},
            {"indoor": False, "mood": ""},
            INDOOR_TAGS,
            False,
            None,
        ),
        (
            "予測降水確率50%以上で屋内タグ",
            {"current": {"precipitation": 0, "apparent_temperature": 20}, "hourly": {"precipitation_probability": 55}},
            {"indoor": False, "mood": ""},
            INDOOR_TAGS,
            False,
            None,
        ),
        (
            "ユーザ屋内希望で屋内タグ",
            {"current": {"precipitation": 0, "apparent_temperature": 25}},
            {"indoor": True, "mood": ""},
            INDOOR_TAGS,
            False,
            None,
        ),
        (
            "体感気温 30℃ で暑さタグ",
            {"current": {"precipitation": 0, "apparent_temperature": 30}},
            {"indoor": False, "mood": ""},
            HOT_TAGS,
            False,
            None,
        ),
        (
            "体感気温 29.9℃ では暑さタグなし",
            {"current": {"precipitation": 0, "apparent_temperature": 29.9}},
            {"indoor": False, "mood": ""},
            set(),
            True,
            None,
        ),
        (
            "寒さ 8℃ で寒さタグ (境界)",
            {"current": {"precipitation": 0, "apparent_temperature": 8}},
            {"indoor": False, "mood": ""},
            COLD_TAGS,
            False,
            None,
        ),
        (
            "寒さ 7.9℃ で寒さタグ",
            {"current": {"precipitation": 0, "apparent_temperature": 7.9}},
            {"indoor": False, "mood": ""},
            COLD_TAGS,
            False,
            None,
        ),
        (
            "冒険気分タグ",
            {"current": {"precipitation": 0, "apparent_temperature": 25}},
            {"indoor": False, "mood": "今日は冒険したい"},
            ADV_TAGS,
            False,
            None,
        ),
        (
            "まったり気分タグ",
            {"current": {"precipitation": 0, "apparent_temperature": 25}},
            {"indoor": False, "mood": "まったり気分"},
            RELAX_TAGS,
            False,
            None,
        ),
        (
            "強風 10m/s で屋内タグ",
            {"current": {"precipitation": 0, "apparent_temperature": 22, "wind_speed_10m": 10}},
            {"indoor": False, "mood": ""},
            INDOOR_TAGS,
            False,
            None,
        ),
        (
            "weatherが不正型で例外",
            "invalid",
            {"indoor": False, "mood": ""},
            None,
            False,
            TypeError,
        ),
    ],
)
def test_shortlist_by_rules(desc, weather, user, expected_subset, expect_empty, raises):
    if raises:
        with pytest.raises(raises):
            shortlist_by_rules(weather, user)
    else:
        result = set(shortlist_by_rules(weather, user))
        if expected_subset:
            assert expected_subset.issubset(result), f"{desc}: subset {expected_subset} not in {result}"
        if expect_empty:
            assert result == set(), f"{desc}: expected empty got {result}"
