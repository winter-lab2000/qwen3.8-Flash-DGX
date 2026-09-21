# VOCAB-PIPELINE · 中文 draft 词表的可重复流程

> 这条流程回答一个问题：**以后语料攒多了，怎么安全地重建词表。**
> 核心约束：**每次重建都必须带 A/B 验证**——窄语料重建可能让词表变差。

## 为什么不能"自动变好"

`VLLM_MTP_DRAFT_VOCAB` 指向的 `.npy` 是**启动时读一次的静态文件**：

- 推理不修改它
- 对话不喂回它
- 草稿头（MTP）权重来自 checkpoint，同样固定

能增长的只有**语料**。所以提升 = 定期重建 + 验证 + 采纳/回退，是个**批处理循环**。
且收益有**天花板**：覆盖率接近饱和后，剩下的失配来自草稿头权重本身，继续重建收益趋零。

## 五步

### 1. 导出语料（在 102，Hermes 所在机器）

```bash
python export_corpus.py
```

产出 `cn_corpus/train/*.txt` 与 `cn_corpus/holdout/holdout.txt`。

**语料选择原则**：主语料取**模型生成文本**——assistant 的 `content` + `reasoning`。
draft 词表要预测的是"模型下一个会生成什么 token"，所以训练分布要贴近生成分布；
工具输出是**输入**、且英文日志占多数，取它会稀释而非帮助。用户提问可收（领域词代表性强、量小）。

**切分**：按消息随机抽 10%。**不要按会话切**——会话数少时（本项目只有 8 个）会把大部分语料抽走。
按消息切会有近重复泄漏、从而高估覆盖率；但两套词表在同一留出集上测，对比结论仍成立。

### 2. 传到 spark-2

```bash
tar czf cn_corpus.tgz cn_corpus
ssh spark-2 'rm -rf ~/flash-next/zh_corpus && mkdir -p ~/flash-next/zh_corpus && tar xzf - -C ~/flash-next/zh_corpus' < cn_corpus.tgz
```

### 3. 构建 + 留出集对照

```bash
spark-2-zh-cn/build_cn_draft_vocab.sh ~/flash-next/qwen3.8-Flash-DGX \
    ~/flash-next/zh_corpus/cn_corpus/train
TOK_DIR=/model/snapshots/<rev> python3 compare_vocab.py
```

**看构建日志里的 `filled from id order up to N`** —— 这是语料规模的诊断指标：
语料里出现的不同 id 越少，按 id 顺序瞎补的槽位越多（本次 46,956 个 id → 约 28% 槽位是填充）。

**不要看**构建工具打印的 `corpus token coverage`：那是自指数字，必然接近 100%，不可用作判据。

### 4. A/B 实测（决定性判据）

```bash
# 4a 基线（出厂词表），需热机
sudo docker stop qwen38-flash && ./flash serve default CTX=262144 YARN=0
./flash wait
ATTR=shipped LABEL=出厂词表 python3 bench_vocab.py

# 4b 换新词表
sudo docker stop qwen38-flash
cp <新词表>.npy ~/.cache/huggingface/draft_vocab_zh_65536.npy
spark-2-zh-cn/serve-zh.sh
./flash wait
# 等服务器热了再测：首发请求含冷 CUDA graph，会明显偏低
ATTR=ours LABEL=新词表 python3 bench_vocab.py
```

判据是 `/metrics` 中 `vllm:spec_decode_num_accepted_tokens_total /
num_draft_tokens_total` 的**增量**——累计值无法隔离本次基准（脚本已做前后快照）。

### 5. 采纳 / 回退

| 判据 | 通过线 |
|---|---|
| **MTP 接受率** | **不下降**（主判据，比 tok/s 干净） |
| decode 中位 tok/s | 提升 |
| 确定性 | greedy 下两次输出逐 token 一致 |

**任何一条不过 → 回退**到出厂词表（`./flash serve default`，一行参数），没有沉没成本。

## 触发条件

不要天天做。建议**语料量翻倍时**或**每 1-2 个月一次**。

**风险**：拿一段很窄的新语料重建，可能挤掉其他内容需要的 id 而**变差**。
所以第 4 步的 A/B 不是可选项。

## 另一条正交的轴

`--n 131072`（词表更大）：覆盖更好，但每步省下的带宽变少。
启动日志里 `MTP reduced draft vocabulary: 65536 of 248320 rows (1212 -> 320 MiB per draft step)`
就是收益来源；N 越大越接近 1212 MiB。中文场景下 65536 与 131072 谁更优**要实测，不能推**。

## 已知的坑

1. **词表必须放在 `$HF_CACHE` 里**——`serve.sh` 只挂载 `$HF_CACHE:/hf`，
   而 `DRAFT_VOCAB` 的值是**原样**当容器内路径使用的。
2. **挂载 tokenizer 时要挂整个模型目录**，不能只挂 snapshot 一层——
   snapshot 里的文件是指向 `../../blobs/` 的符号链接，只挂一层会断。
3. **首次请求偏慢**（冷 CUDA graph）：A/B 要等服务器热了再测，或丢掉第一发。
4. **容器以 root 写缓存**，必要时 `sudo chown -R "$(id -u):$(id -g)" ~/.cache/huggingface`。
5. **别让两个下载/推理任务同时抢**：本条线路 4 MB/s、单卡一次只跑一个大模型。

## 实测记录

2026-09-21 首次执行：接受率 27.33% → 49.62%，decode 中位 24.03 → 31.92 tok/s。
完整数据与可信度证据见 `docs/00-feasibility-verification.md` §G8。
