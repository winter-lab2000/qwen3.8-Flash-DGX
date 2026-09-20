# 03 · 中文 MTP draft 词表

> 本项目**唯一需要自研**的一项。其余全部是既有方案的配置。

## 1. 机制：为什么一个"词表"能影响 20% 的解码速度

Flash-Next 自带一个 MTP（多 token 预测）投机解码头。每个引擎步里，草稿头都要为自己的 `lm_head` 打分
——而那个 `lm_head` 是 **248,320 行**（全词表），每步读一次。

**缩减词表**做的事：只让草稿头对 **65,536 个最高频 token id** 打分，其余直接屏蔽。

- 省下的带宽：切到 65,536 行，每步省 **2.61 GiB**；Spark 上解码贴近带宽墙，**省下的字节几乎一比一换成时间** [5]
- 实测收益：**+13.1%（8 格均值）到 +25.8%（单流 prose）** [2][5]
- **正确性不受影响**：目标模型仍然对**全词表**验证每一个被草稿提出的 token。
  一个不在词表里的 token，**永远不会被草稿提出**——它只是让目标多拒绝一次 [4]

这就是关键：**代价是"慢"，不是"错"。** 所以这是一项零风险的速度优化。

## 2. 问题：出厂词表是英文+代码语料构建的

MiaAI-Lab 的 README 自己写明了 [5]（原文）：

> *"The shipped file was built from host code and docs, not from the model's own output, so non-code traffic
> (**especially Chinese, where the measured 65k vocab covered only 50.6%**) may draft worse than the numbers
> above; correctness is unaffected, only speed."*

**覆盖率 50.6% 的含义**：中文文本里每 2 个 token，就有 1 个落在那 65,536 个 id 之外。
草稿头**提不出**这些 token，于是提名命中率下降，那 +20% 的收益对中文拿不满。

blazux 的 Dockerfile 里也留了同一句话 [3]：

> *"The id set = the 65,536 most frequent tokens (corpus + BPE order) plus every special/added token;
> **rebuild it for another language mix with `tools/build_draft_vocab.py`**."*

**两边都指向同一个动作：用你自己的语料重建。** 而没人做过中文版。

## 3. 构建工具：现成的，接口正好合用

`tools/build_draft_vocab.py` [4]：

```
usage: build_draft_vocab.py <tokenizer_dir> <out.npy> [--n 65536] [--corpus path ...]
```

它的选 id 规则（源码要点）：

1. 语料里出现过的 token，**按词频**排序取（→ 领域适配就靠这一步）
2. **所有 special / added token 永远保留**（chat template、tool-call 标记、thinking 标记）
3. **前 256 个 id 永远保留**（byte fallback）
4. 剩余名额用**最低 id 补满**（Qwen 的 BPE 合并顺序是频率的代理）
5. 输出：排序后的 `int32 .npy`
6. **并打印 `corpus token coverage NN.NNN%`** ← 就是我们需要的那个指标

支持的语料格式：目录递归，扩展名 `.json .md .txt .py .log .jsonl`，单文件 < 20 MB。
`.json` 会递归遍历所有字符串值——**这对 Hermes 的会话导出特别友好**。

## 4. 语料选择（这一步决定成败）

优先级从高到低：

1. **本机 Hermes 的真实会话记录** —— 最贴合实际工作负载。MiaAI 明确指出他们**没有**用真实输出做语料 [5]，
   这正是我们能超过它的地方。
2. 中文技术文档 / 笔记（`.md`）
3. 中文注释的源码（`.py`）
4. 通用中文文本

**注意**：工具按"字符数 × 词频"统计，所以语料要**足够大**（目标是几千万字符量级）才有统计意义。
语料太小 → 覆盖率的统计噪声大 → 得到的词表过拟合到少量文本。

## 5. 验收（不接受"看起来更快了"）

| 判据 | 通过线 | 怎么测 |
|---|---|---|
| C1 语料覆盖率 | **≥ 80%**（参照：出厂词表中文 50.6%，英文+代码语料 99.58%）[5] | 构建工具自己打印 |
| C2 中文 decode | 相对出厂词表 **≥ +5%** | 同一批中文 prompt，各 ≥3 次取中位 |
| C3 draft 接受率 | **不下降** | 引擎日志/metrics 的 acceptance 计数 |
| C4 正确性 | 输出**逐 token 一致**（T=0） | 同 prompt 两次请求比对 |
| C5 长任务不退化 | 20 轮 agent 场景总耗时下降 | `docs/02` T4 的验收脚本扩展 |

**任何一条不通过 → 回退出厂词表**，并在 `notes/task5-vocab-ab.txt` 记录实测值。
回退是一行参数的事（`DRAFT_VOCAB=1`），没有沉没成本。

## 6. 已知的不确定性（诚实记录）

- **覆盖率 → 接受率不是线性关系。** 覆盖率从 50.6% 提到 90% 不代表 decode 就 +18%。
  接受率取决于"目标端真正想生成的下一个 token"有多少落在词表内，这跟文本类型强相关。**必须实测。**
- **`--n` 可以调。** 更大（如 131072）覆盖更好但省下的带宽变少；更小反之。
  默认 65536 是上游在英文+代码上测出的平衡点 [3]，中文场景**值得把 65536 与 131072 各测一次**。
- **special token 一定在集合内**，所以 tool-call / thinking 标记不受影响——不会因为换词表而破坏 agent 的协议输出。
- 工具用 `transformers` 的 tokenizer，需要目标 tokenizer 目录（就是 checkpoint snapshot 本身）。

## 7. 操作步骤

见 `scripts/build_cn_draft_vocab.sh`（T5 Step 3 的执行体）与 `docs/02` Task 5 的完整验收流程。

---

## Sources

[1] https://github.com/blazux/qwen3.8-Flash-DGX
[2] https://raw.githubusercontent.com/blazux/qwen3.8-Flash-DGX/main/scripts/serve.sh
[3] https://raw.githubusercontent.com/blazux/qwen3.8-Flash-DGX/main/Dockerfile
[4] https://raw.githubusercontent.com/blazux/qwen3.8-Flash-DGX/main/tools/build_draft_vocab.py
[5] https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark
