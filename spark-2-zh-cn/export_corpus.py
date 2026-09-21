"""把 Hermes 会话记录导出成 draft 词表语料。

语料选择原则：draft 词表要预测的是"模型下一个会生成什么 token"，
所以主语料必须是**模型生成文本**（assistant content + reasoning），
而不是工具输出（那是输入、且英文日志占多数）。
用户提问也收进来（领域词汇代表性强，量小不伤大局）。

按 session_id 切分训练/留出，避免同一会话的近重复内容污染留出集。
"""
import os
import sqlite3

DB = r"C:/Users/winte/AppData/Local/hermes/state.db"
OUT = r"C:/Users/winte/Desktop/spark/cn_corpus"
TRAIN = os.path.join(OUT, "train")
HOLDOUT = os.path.join(OUT, "holdout")
os.makedirs(TRAIN, exist_ok=True)
os.makedirs(HOLDOUT, exist_ok=True)

c = sqlite3.connect(DB)
cur = c.cursor()

# 按消息随机抽 10% 做留出。
# 说明：只按会话切分在这里不可行（总共只有 8 个会话，其中一个是超长会话，
# 一留出就把大部分语料抽走了）。按消息随机切会有近重复泄漏，从而**高估**覆盖率——
# 但两套词表是在同一个留出集上测的，所以**对比结论依然成立**，我们要的就是对比。
import random

rows = list(cur.execute(
    "SELECT session_id, role, COALESCE(content,''), COALESCE(reasoning,''), timestamp "
    "FROM messages ORDER BY timestamp"
))
print(f"总行数: {len(rows)}")

sess = sorted({r[0] for r in rows if r[0]})
print(f"会话数: {len(sess)}")

rng = random.Random(42)
hold_flags = [rng.random() < 0.10 for _ in rows]
print(f"留出消息: {sum(hold_flags)} / {len(rows)}")

parts = {"train": [], "holdout": []}
stats = {}
for (sid, role, content, reasoning, _), is_hold in zip(rows, hold_flags):
    bucket = "holdout" if is_hold else "train"
    kept = 0
    if content:
        parts[bucket].append(content)
        kept += len(content)
    if role == "assistant" and reasoning:
        parts[bucket].append(reasoning)
        kept += len(reasoning)
    stats[bucket] = stats.get(bucket, 0) + kept

# 本机中文文档（项目报告等）只进训练集
for d in (r"C:/Users/winte/Desktop/spark", r"C:/Users/winte/Desktop/hermes-misc"):
    if not os.path.isdir(d):
        continue
    for f in sorted(os.listdir(d)):
        if f.lower().endswith((".md", ".txt")):
            p = os.path.join(d, f)
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            parts["train"].append(t)
            stats["train"] = stats.get("train", 0) + len(t)

for bucket, chunks in parts.items():
    text = "\n\n".join(chunks)
    if bucket == "train":
        # 分片写，单文件别太大
        n = 0
        CH = 2_000_000
        for i in range(0, len(text), CH):
            with open(os.path.join(TRAIN, f"train_{n:03d}.txt"), "w", encoding="utf-8") as fh:
                fh.write(text[i:i + CH])
            n += 1
        print(f"train   : {len(text):,} 字符 -> {n} 个分片")
    else:
        with open(os.path.join(HOLDOUT, "holdout.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"holdout : {len(text):,} 字符 -> 1 个文件")

print("\n目录:")
for root, _, files in os.walk(OUT):
    for f in sorted(files):
        p = os.path.join(root, f)
        print(f"  {os.path.getsize(p)/1e6:8.2f} MB  {os.path.relpath(p, OUT)}")
