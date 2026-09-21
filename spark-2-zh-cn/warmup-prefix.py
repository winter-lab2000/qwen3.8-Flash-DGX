"""服务重启后预热前缀缓存。

原理：前缀缓存按前缀**逐字节**匹配，住在 GPU 显存里，服务一重启就清空。
而 Hermes 的 system prompt 是**跨会话共享**的（同一份 base + skills + memory，
且在 state.db 的 system_prompts 表里按 hash 内容寻址）。
所以拿真实的 system prompt 各发一发 max_tokens=1 的请求，就能把公共前缀灌进缓存，
之后**每个新会话**的第一轮都直接命中 —— 省掉一次约 8-9 秒的冷 prefill。

用法：
    python warmup-prefix.py --api http://172.16.1.28:18300
    python warmup-prefix.py --api http://172.16.1.28:18300 --limit 3

注意：预热**只对与服务端缓存里那份逐字节相同的**前缀有效。
      若预热后又改了 skills/memory（system prompt 随之变化），预热就白做了 —— 重跑一次。
"""
import argparse
import json
import os
import sqlite3
import time
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://172.16.1.28:18300")
    ap.add_argument("--model", default="qwen3.8-flash-next")
    ap.add_argument("--db", default=os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                                 "hermes", "state.db"))
    ap.add_argument("--limit", type=int, default=5,
                    help="最多预热几条不同的 system prompt（按使用次数取前 N）")
    ap.add_argument("--min-chars", type=int, default=2000,
                    help="短于此长度的忽略（不值得预热）")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        raise SystemExit(f"!! 找不到 state.db: {a.db}")

    c = sqlite3.connect(a.db)
    rows = c.execute(
        "SELECT prompt, COUNT(*) AS n FROM system_prompts "
        "WHERE LENGTH(prompt) >= ? GROUP BY prompt ORDER BY n DESC LIMIT ?",
        (a.min_chars, a.limit),
    ).fetchall()
    if not rows:
        raise SystemExit("!! system_prompts 表里没有够长的条目")

    print(f"待预热 {len(rows)} 条 system prompt（按被多少会话用过排序）")
    url = a.api.rstrip("/") + "/v1/chat/completions"
    total = 0.0
    for i, (prompt, cnt) in enumerate(rows, 1):
        payload = {
            "model": a.model,
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode())
            dt = time.time() - t0
            pt = (d.get("usage") or {}).get("prompt_tokens")
            print(f"  [{i}/{len(rows)}] {len(prompt):>7,} 字符 / {pt:>6} tokens  "
                  f"服务过 {cnt} 个会话  预热 {dt:5.2f}s")
            total += dt
        except Exception as e:
            print(f"  [{i}/{len(rows)}] 失败: {type(e).__name__}: {str(e)[:120]}")

    print(f"\n预热完成，总耗时 {total:.1f}s —— 这就是接下来每个新会话第一轮省下的冷 prefill")
    try:
        with urllib.request.urlopen(a.api.rstrip("/") + "/metrics", timeout=20) as r:
            for line in r.read().decode().splitlines():
                if (line.startswith("vllm:prefix_cache_hits_total")
                        or line.startswith("vllm:prefix_cache_queries_total")):
                    print("  ", line)
    except Exception:
        pass


if __name__ == "__main__":
    main()
