# spark-2-zh-cn — 我们的 delta

本目录是**相对上游的唯一改动**，刻意放在独立目录里：`git pull upstream main` 永远不会冲突。

上游默认值已经就是我们要的，所以这里**没有改任何上游文件**：

| 需求 | 上游默认 | 我们要动吗 |
|---|---|---|
| 上下文 262,144 | `CTX=262144`、`YARN=0` | 否 |
| 前缀缓存（初响应） | `PREFIX_CACHE=1`（含 block_size 修复） | 否 |
| prefill chunk | `--max-num-batched-tokens 8192` | 否 |
| 官方 NVFP4 权重 | `nvidia/Qwen3.8-Flash-Next-NVFP4`（9/14 起） | 否 |
| 确定性 top-k / effort 别名 | `DET_TOPK=1` / `EFFORT_ALIAS=1` | 否 |
| **中文 MTP draft 词表** | 出厂词表英文+代码语料，中文覆盖 **50.6%** | **是 ← 本目录** |
| Hermes 侧前缀纪律 | 无（客户端侧） | 是（见 project repo）|

## 用法

```bash
# 1) 构建中文词表（会打印 corpus token coverage，这就是验收指标）
spark-2-zh-cn/build_cn_draft_vocab.sh ~/flash-next/qwen3.8-Flash-DGX ~/flash-next/zh_corpus

# 2) 用它启动（其余参数保持上游默认）
cd ~/flash-next/qwen3.8-Flash-DGX
MODE=hybrid DRAFT_VOCAB=$HOME/flash-next/draft_vocab_zh_65536.npy ./scripts/serve.sh
```

## 验收（不达标就回退到出厂词表）

| 判据 | 通过线 |
|---|---|
| 语料覆盖率 | ≥ 80%（出厂词表中文 50.6% / 英文+代码 99.58%） |
| 中文 decode | ≥ +5%（同批 prompt，≥3 次取中位） |
| draft 接受率 | 不下降 |
| T=0 输出 | 逐 token 一致 |

**正确性永远不受影响**：目标模型对**全词表**验证每个被草稿提出的 token，
词表外 token 永远不会被草稿提出——最坏情况只是变慢。回退成本是一行参数。

详见 `spark-2-zh-cn/CN-DRAFT-VOCAB.md`。

## 项目追踪

本 fork 只放**代码与配方**。部署状态、可行性证据、实施计划与验收记录在：

**https://github.com/winter-lab2000/qwen38-flash-next-spark**

## 许可

上游为 Apache-2.0。本目录同样按 Apache-2.0 提供，未修改任何上游文件。
