import os, json, numpy as np, pytest, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 既存副作用防止のため最低限の埋め込みファイル準備 (tests/test_rules.py と同様)
if not os.path.exists("activities_seed.json"):
    with open("activities_seed.json", "w", encoding="utf-8") as f:
        json.dump([{"name":"Dummy", "tags":["x"]}], f, ensure_ascii=False)
if not os.path.exists("embeddings.npy"):
    np.save("embeddings.npy", np.zeros((1,4), dtype=np.float32))

from app import shortlist_by_rules  # noqa: E402

INDOOR_BASE = {"indoor","museum","cinema","boardgame","spa","arcade"}
ADV = {"bouldering","trampoline","karaoke"}
RELAX = {"cafe","bookstore"}
COLD = {"sauna","cafe","spa"}

# 新仕様ルール:
# - 雨(>0mm) or 予測降水確率>=50% or indoor希望 or 風速>=10m/s で屋内タグ
# - 体感>=30℃ で暑さ回避タグ(aquarium,mall)
# - 体感<=8℃ で寒さタグ(sauna,cafe,spa)
# - 気分: 冒険 / まったり

@pytest.mark.parametrize(
    "case,weather,user,expect_subset",
    [
        ("予測降水確率で屋内", {"current":{"precipitation":0,"apparent_temperature":18},"hourly":{"precipitation_probability":80}}, {"indoor":False,"mood":""}, INDOOR_BASE),
        ("寒さ境界8度で寒さタグ", {"current":{"precipitation":0,"apparent_temperature":8}}, {"indoor":False,"mood":""}, COLD),
        ("強風で屋内", {"current":{"precipitation":0,"apparent_temperature":15,"wind_speed_10m":10}}, {"indoor":False,"mood":""}, INDOOR_BASE),
        ("暑さ境界30でaquarium/mall", {"current":{"precipitation":0,"apparent_temperature":30}}, {"indoor":False,"mood":""}, {"aquarium","mall"}),
        ("降水0は屋内付かない", {"current":{"precipitation":0,"apparent_temperature":20}}, {"indoor":False,"mood":""}, set()),
        ("気分複合", {"current":{"precipitation":0,"apparent_temperature":22}}, {"indoor":False,"mood":"まったり冒険"}, ADV | RELAX),
    ]
)

def test_shortlist_spec(case, weather, user, expect_subset):
    tags = set(shortlist_by_rules(weather, user))
    if expect_subset:
        assert expect_subset.issubset(tags), f"{case}: expected subset {expect_subset} in {tags}"
    else:
        assert tags == set(), f"{case}: expected empty got {tags}"
