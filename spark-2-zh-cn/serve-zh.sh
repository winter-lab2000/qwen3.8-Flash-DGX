#!/usr/bin/env bash
# 用中文 draft 词表启动 Flash-Next —— 本项目唯一相对上游的行为改动。
#
# 为什么需要它（实测，2026-09-21）：
#   出厂 draft 词表是英文+代码语料构建的。中文流量下 MTP 接受率只有 27.33%。
#   用本机 Hermes 会话记录重建（中文行覆盖率 77.67% -> 99.85%）后：
#       MTP 接受率 27.33% -> 49.62%   (+81.6% 相对)
#       decode     24.03  -> 31.92 tok/s (中位，+32.8%)
#           zh-prose 21.32 -> 31.92 (+49.7%)
#           zh-tech  24.00 -> 32.00 (+33.3%)
#           mixed    29.96 -> 35.04 (+17.0%)
#
# 关键坑：serve.sh 只把 $HF_CACHE 挂进容器，而 DRAFT_VOCAB 的值是**原样**
# 当容器内路径用的 —— 所以自定义词表必须放在 $HF_CACHE 目录里，否则容器读不到。
#
# 用法：
#   spark-2-zh-cn/serve-zh.sh                    # 推荐配方（262144 + YARN=0 + 中文词表）
#   spark-2-zh-cn/serve-zh.sh CTX=500000 YARN=1  # 临时换回上游默认上下文
set -euo pipefail

cd "$(dirname "$0")/.."

VOCAB_HOST="${VOCAB_HOST:-$HOME/.cache/huggingface/draft_vocab_zh_65536.npy}"
VOCAB_CTR="${VOCAB_CTR:-/hf/draft_vocab_zh_65536.npy}"

if [ ! -s "$VOCAB_HOST" ]; then
  cat >&2 <<EOF
!! 找不到中文词表: $VOCAB_HOST

构建方式（见 spark-2-zh-cn/VOCAB-PIPELINE.md）：
  1) 导出语料:  python export_corpus.py
  2) 构建词表:  spark-2-zh-cn/build_cn_draft_vocab.sh
  3) 放到挂载点: cp <out.npy> $VOCAB_HOST
回退到出厂词表则直接用 ./flash serve default
EOF
  exit 1
fi

echo ">> 中文词表: $VOCAB_HOST   (容器内路径 $VOCAB_CTR)"
echo ">> 上下文/其他参数: CTX=262144 YARN=0（与 27B 对齐、避开 YaRN）"
exec ./flash serve default CTX=262144 YARN=0 DRAFT_VOCAB="$VOCAB_CTR" "$@"
