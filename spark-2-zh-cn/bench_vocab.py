"""MTP draft 词表 A/B 基准。

固定一组中文/中英混合 prompt，greedy 解码，测端到端 tok/s，
并尽量读取引擎的投机解码接受率计数。

用法：
  ATTR=shipped LABEL=出厂词表 python3 bench_vocab.py
  ATTR=ours    LABEL=新词表   python3 bench_vocab.py
结果追加到 /out/vocab_ab.jsonl
"""
import json
import os
import time
import urllib.request

API = "http://127.0.0.1:18300"
ATTR = os.environ.get("ATTR", "unknown")
LABEL = os.environ.get("LABEL", ATTR)
OUT = os.environ.get("OUT", "/out/vocab_ab.jsonl")
REPS = int(os.environ.get("REPS", "3"))

# 代表性负载：中文说明文 / 中文技术解释 / 中英混合
PROMPTS = [
    ("zh-prose", "用中文写一段 300 字左右的说明文，介绍长江的地理特征与水文特点。"),
    ("zh-tech", "用中文详细解释一下什么是前缀缓存（prefix caching），以及它在长对话推理里为什么能显著降低首 token 延迟。"),
    ("mixed", "用中文总结一下 vLLM 里 MTP（multi-token prediction）投机解码的工作原理，可以夹带英文术语。"),
]


def post(path, payload, timeout=300):
    req = urllib.request.Request(
        API + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get(path, timeout=30):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return r.read().decode()


def spec_snapshot():
    """抓投机解码计数快照，用于算增量（累计值不隔离本次基准）。"""
    try:
        txt = get("/metrics")
    except Exception:
        return None
    keys = ("num_drafts_total", "num_draft_tokens_total", "num_accepted_tokens_total")
    snap = {}
    for line in txt.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if not line.startswith("vllm:spec_decode_"):
            continue
        name = line.rsplit(" ", 1)[0]
        val = line.rsplit(" ", 1)[-1]
        try:
            fv = float(val)
        except ValueError:
            continue
        for k in keys:
            if f"spec_decode_{k}" in name and "per_pos" not in name:
                snap[k] = fv
        if "accepted_tokens_per_pos_total" in name and 'position="0"' in name:
            snap["pos0"] = fv
        if "accepted_tokens_per_pos_total" in name and 'position="1"' in name:
            snap["pos1"] = fv
    return snap or None


def spec_delta(before, after):
    if not before or not after:
        return None
    d = {k: after.get(k, 0) - before.get(k, 0) for k in after}
    drafts = d.get("num_drafts_total", 0)
    dtok = d.get("num_draft_tokens_total", 0)
    acc = d.get("num_accepted_tokens_total", 0)
    if dtok <= 0:
        return None
    return {
        "drafts": drafts, "draft_tokens": dtok, "accepted": acc,
        "accept_rate": acc / dtok * 100,
        "pos0_rate": (d.get("pos0", 0) / drafts * 100) if drafts else 0,
        "pos1_rate": (d.get("pos1", 0) / d.get("pos0", 1) * 100) if d.get("pos0") else 0,
    }


before = spec_snapshot()
rows = []
print(f"=== A/B: {LABEL} (ATTR={ATTR}) ===")
for tag, prompt in PROMPTS:
    for rep in range(REPS):
        t0 = time.time()
        try:
            d = post("/v1/chat/completions", {
                "model": "qwen3.8-flash-next",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400, "temperature": 0,
            })
        except Exception as e:
            print(f"  {tag} rep{rep}: 失败 {type(e).__name__}: {str(e)[:90]}")
            continue
        dt = time.time() - t0
        u = d.get("usage") or {}
        ct = u.get("completion_tokens", 0)
        rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        fps = ct / dt if dt else 0
        rows.append({"attr": ATTR, "label": LABEL, "tag": tag, "rep": rep,
                     "secs": round(dt, 3), "completion_tokens": ct,
                     "reasoning_tokens": rt, "tok_s": round(fps, 2),
                     "finish": d["choices"][0].get("finish_reason")})
        print(f"  {tag:9} rep{rep}  {ct:4d} tok / {dt:6.2f}s = {fps:5.2f} tok/s  "
              f"(reasoning {rt})")

with open(OUT, "a", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")

if rows:
    ok = [r for r in rows if r["finish"] != "length"]
    use = [r for r in rows if r["finish"] != "length"] or rows
    vals = sorted(r["tok_s"] for r in use)
    med = vals[len(vals) // 2]
    print(f"\n  {LABEL}: n={len(rows)}  (未截断 {len(ok)})")
    print(f"  中位 tok/s = {med:.2f}   均值 = {sum(vals)/len(vals):.2f}   范围 {vals[0]:.2f}-{vals[-1]:.2f}")

after = spec_snapshot()
delta = spec_delta(before, after)
if delta:
    print("\n  本次基准的 MTP 接受率增量:")
    print(f"    drafts={delta['drafts']:.0f}  draft_tokens={delta['draft_tokens']:.0f}  "
          f"accepted={delta['accepted']:.0f}")
    print(f"    >>> 接受率 = {delta['accept_rate']:.2f}%   "
          f"(pos0 {delta['pos0_rate']:.2f}%  pos1 {delta['pos1_rate']:.2f}%)")
else:
    print("\n  （未抓到 spec-decode 增量——以 tok/s 为准）")

med = None
if rows:
    v = sorted(r["tok_s"] for r in rows)
    med = v[len(v) // 2]

with open(OUT, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"summary": True, "attr": ATTR, "label": LABEL,
                         "n": len(rows), "tok_s_median": med, "spec": delta},
                        ensure_ascii=False) + "\n")
print(f"\n  结果已追加到 {OUT}")
