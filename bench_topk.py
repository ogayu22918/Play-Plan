import time, statistics, random, string
import numpy as np
from app import top_k_by_embedding, EMB_UNIT, ACTIVITIES, client, EMBEDDING_MODEL

# ダミークエリを複数生成 (Gemini埋め込み呼び出しは高コストなので1回のみ計測例) 
# 実運用では埋め込みAPI遅延が支配的なため、ここではベクトル計算部分のベンチ用に
# 擬似クエリベクトルで計測する関数を追加する。

# 内部実装に合わせた計測: EMB_UNIT @ q_unit と argpartition

def bench_vector_path(repeat=200, dim=None):
    if dim is None:
        # 既存ベクトル次元
        dim = EMB_UNIT.shape[1]
    rng = np.random.default_rng(123)
    times=[]
    for _ in range(repeat):
        q = rng.standard_normal(dim).astype(np.float32)
        q /= (np.linalg.norm(q)+1e-9)
        t0 = time.perf_counter()
        sims = EMB_UNIT @ q
        k=12
        idx = np.argpartition(sims, -k)[-k:]
        idx = idx[np.argsort(sims[idx])[::-1]]
        _ = idx  # use
        t1 = time.perf_counter()
        times.append((t1-t0)*1000)
    print(f"Vector path: N={EMB_UNIT.shape[0]} D={dim} repeat={repeat}")
    print(f"  mean={statistics.mean(times):.3f}ms median={statistics.median(times):.3f}ms p95={np.percentile(times,95):.3f}ms min={min(times):.3f}ms")

if __name__ == '__main__':
    # API呼び出しを含む1回 (遅延参考)
    t0=time.perf_counter()
    r = top_k_by_embedding('カフェでまったり 本 読書', k=12)
    t1=time.perf_counter()
    print('Full path (with embed API) elapsed: %.3f ms (may dominate)' % ((t1-t0)*1000))
    # 純計算ベンチ
    bench_vector_path()
