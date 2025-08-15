import os, json, numpy as np, pytest

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

# 仕様 (1行要約):
# - 雨(>0mm) または 予測降水確率>=60% または indoor希望 で屋内タグ
# - 体感>=30℃ で暑さ回避タグ(aquarium,mall)
# - 体感<=5℃ で暖か系(spa) を追加 (雨等なくても)
# - 風速>=12m/s で屋内安全タグ(cinema,museum,indoor)
# - 気分文字列に "冒険" / "まったり" を含めば各タグ集合
# - 重複除去

@pytest.mark.parametrize(
    "case,weather,user,expect_subset",
    [
        ("予測降水確率で屋内", {"current":{"precipitation":0,"apparent_temperature":18},"hourly":{"precipitation_probability":80}}, {"indoor":False,"mood":""}, INDOOR_BASE),
        ("寒さ境界5度でspa", {"current":{"precipitation":0,"apparent_temperature":5}}, {"indoor":False,"mood":""}, {"spa"}),
        ("強風でcinema", {"current":{"precipitation":0,"apparent_temperature":15,"wind_speed_10m":12}}, {"indoor":False,"mood":""}, {"cinema"}),
        ("暑さ境界30でaquarium", {"current":{"precipitation":0,"apparent_temperature":30}}, {"indoor":False,"mood":""}, {"aquarium"}),
        ("降水0は屋内付かない", {"current":{"precipitation":0,"apparent_temperature":20}}, {"indoor":False,"mood":""}, set()),
        ("気分複合", {"current":{"precipitation":0,"apparent_temperature":22}}, {"indoor":False,"mood":"まったり冒険"}, ADV | RELAX),
    ]
)
def test_shortlist_spec(case, weather, user, expect_subset):
    tags = set(shortlist_by_rules(weather, user))
    # 期待集合が空でなければ部分集合として含まれる (屋内は他の条件で追加されうる)
    if expect_subset:
        assert expect_subset.issubset(tags), f"{case}: expected subset {expect_subset} in {tags}"
    else:
        # 完全に空期待のとき、既存仕様で不要な追加タグがないか (将来ルール追加時は必要に応じ更新)
        assert tags == set(), f"{case}: expected empty got {tags}"
