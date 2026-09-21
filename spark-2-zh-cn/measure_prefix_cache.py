"""用 Hermes 真实的 system prompt 量前缀缓存的真实收益。

比合成一个假前缀有意义：缓存按前缀逐字节匹配，用真 prompt 才能反映实际收益。
"""
import json
import os
import sqlite3
import time
import urllib.request

DB = os.path.join(os.environ["LOCALAPPDATA"], "hermes", "state.db")
API = "http://172.16.1.28:18300/v1/chat/completions"

c = sqlite3.connect(DB)
cur = c.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(system_prompts)")]
print("system_prompts 列:", cols)

textcol = next((x for x in ("prompt", "content", "text", "system_prompt") if x in cols), None)
if not textcol:
    raise SystemExit("!! 找不到文本列")

row = cur.execute(
    f"SELECT {textcol} FROM system_prompts ORDER BY LENGTH({textcol}) DESC LIMIT 1"
).fetchone()
sysmsg = row[0] if row else None
if not sysmsg:
    raise SystemExit("!! system_prompts 表是空的")

print(f"取到最长的一条 system prompt: {len(sysmsg):,} 字符")
print("开头 200 字符:", repr(sysmsg[:200]))

QUESTION = "请用一句话说明前缀缓存为什么能降低首 token 延迟。"


def call(label):
    payload = {
        "model": "qwen3.8-flash-next",
        "messages": [
            {"role": "system", "content": sysmsg},
            {"role": "user", "content": QUESTION},
        ],
        "max_tokens": 120,
        "temperature": 0,
    }
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read().decode()
    dt = time.time() - t0
    d = json.loads(raw)
    u = d.get("usage") or {}
    print(f"  {label:22} 端到端 {dt:6.2f}s   prompt_tokens={u.get('prompt_tokens')}  "
          f"completion={u.get('completion_tokens')}")
    return dt, u.get("prompt_tokens")


print("\n=== 同一前缀（真实 system prompt）连发三次 ===")
print("  说明：第 1 次付冷 prefill；第 2/3 次应命中前缀缓存")
r1 = call("第 1 次（冷）")
r2 = call("第 2 次")
r3 = call("第 3 次")

if r1[0] > 0:
    print(f"\n  冷 vs 热：{r1[0]:.2f}s -> {min(r2[0], r3[0]):.2f}s  "
          f"= 快 {r1[0]/max(min(r2[0], r3[0]), 1e-6):.1f}×  "
          f"（每轮省 {r1[0]-min(r2[0], r3[0]):.2f}s）")
    print(f"  折算 20 轮长任务：省约 {20*(r1[0]-min(r2[0], r3[0])):.0f}s")

# 顺带看看引擎侧的前缀缓存命中计数
try:
    with urllib.request.urlopen("http://172.16.1.28:18300/metrics", timeout=20) as r:
        for line in r.read().decode().splitlines():
            if "prefix_cache" in line and not line.startswith("#"):
                print("  ", line)
except Exception as e:
    print("  metrics 取不到:", e)
