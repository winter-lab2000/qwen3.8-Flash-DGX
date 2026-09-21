"""在同一个留出集上对比「出厂 draft 词表」与「新构建的中文 draft 词表」。

为什么这么做而不是只看构建工具打印的 "corpus token coverage"：
那个数字是自指的——工具从语料里按词频取 top-N，所以它对同一批语料的覆盖率
必然很高（Zipf 决定的），好看但不可迁移。留出集上的覆盖率才有可比性。
"""
import os
import sys

import numpy as np
from transformers import AutoTokenizer

TOK_DIR = os.environ.get("TOK_DIR", "/tok")
HOLDOUT = "/corpus/cn_corpus/holdout/holdout.txt"
TRAIN_DIR = "/corpus/cn_corpus/train"
SHIPPED = "/repo/src/draft_vocab_65536.npy"
OURS = "/out/new_vocab_65536.npy"

if not os.path.exists(OURS):
    sys.exit(f"!! 新词表不存在: {OURS}（先跑 build_draft_vocab.py）")

tok = AutoTokenizer.from_pretrained(TOK_DIR, trust_remote_code=True)
VOCAB = getattr(tok, "vocab_size", None) or len(tok)

shipped = np.load(SHIPPED)
ours = np.load(OURS)
sset = {int(x) for x in shipped}
oset = {int(x) for x in ours}

print(f"tokenizer vocab_size : {VOCAB:,}")
print(f"出厂词表             : {len(shipped):,} 个 id  ({shipped.dtype})")
print(f"新词表               : {len(ours):,} 个 id  ({ours.dtype})")
inter = len(sset & oset)
print(f"两者交集             : {inter:,}  ({inter/len(oset)*100:.1f}% 的新词表 id 与出厂重合)")
print(f"新词表独有           : {len(oset - sset):,}")
print(f"出厂独有被替换掉的   : {len(sset - oset):,}")


def stat(path, label):
    text = open(path, encoding="utf-8", errors="replace").read()
    ids = tok.encode(text)
    n = len(ids)
    if n == 0:
        return None
    cs = sum(1 for i in ids if i in sset)
    co = sum(1 for i in ids if i in oset)
    print(f"\n=== {label} ===")
    print(f"  字符数            : {len(text):,}")
    print(f"  token 数          : {n:,}")
    print(f"  出厂词表覆盖      : {cs/n*100:6.2f}%")
    print(f"  新词表覆盖        : {co/n*100:6.2f}%")
    print(f"  增益              : {(co-cs)/n*100:+6.2f} 个百分点")
    print(f"  相对提升          : {(co/max(cs,1)-1)*100:+6.1f}%")
    return n, cs, co


stat(HOLDOUT, "留出集（决定性）")
stat(os.path.join(TRAIN_DIR, "train_000.txt"), "训练集分片（自指，仅参考）")

# 中文为主的那部分单独看：只统计含 CJK 的行，逼近"中文流量"场景
print("\n=== 只看含中文的行（更贴近中文流量）===")
lines = [ln for ln in open(HOLDOUT, encoding="utf-8", errors="replace")
         if any("\u4e00" <= ch <= "\u9fff" for ch in ln)]
zh = "\n".join(lines)
if zh:
    ids = tok.encode(zh)
    n = len(ids)
    cs = sum(1 for i in ids if i in sset)
    co = sum(1 for i in ids if i in oset)
    print(f"  行数 {len(lines):,}  token {n:,}")
    print(f"  出厂词表覆盖      : {cs/n*100:6.2f}%")
    print(f"  新词表覆盖        : {co/n*100:6.2f}%")
    print(f"  增益              : {(co-cs)/n*100:+6.2f} 个百分点")
else:
    print("  （留出集里没有含中文的行？）")
