#!/usr/bin/env bash
# 构建中文 MTP draft 词表（Qwen3.8-Flash-Next on DGX Spark）
#
# 用法：
#   ./build_cn_draft_vocab.sh <repo_dir> <corpus_dir_or_files...>
# 例：
#   ./build_cn_draft_vocab.sh ~/flash-next/qwen3.8-Flash-DGX ~/flash-next/zh_corpus
#
# 依赖：spark 上的 python3 + transformers + numpy（checkpoint 的 vLLM 镜像里都有；
#       宿主机没有就把本脚本用 `docker run --rm -v ...` 的方式跑，见文件末尾注释）。
#
# 产出：~/flash-next/draft_vocab_zh_65536.npy 和 ~/flash-next/draft_vocab_zh_131072.npy
#       以及一份带覆盖率的日志。
set -euo pipefail

REPO="${1:-}"
shift || true
CORPUS=("$@")

if [ -z "$REPO" ] || [ ! -d "$REPO" ]; then
  echo "usage: $0 <repo_dir> <corpus...>" >&2
  exit 2
fi
if [ "${#CORPUS[@]}" -eq 0 ]; then
  echo "error: 至少给一个语料路径" >&2
  exit 2
fi
for c in "${CORPUS[@]}"; do
  [ -e "$c" ] || { echo "error: 语料不存在: $c" >&2; exit 2; }
done

BUILDER="$REPO/tools/build_draft_vocab.py"
[ -f "$BUILDER" ] || { echo "error: 找不到 $BUILDER" >&2; exit 2; }

MODEL_ID="nvidia/Qwen3.8-Flash-Next-NVFP4"
HUB="$HOME/.cache/huggingface/hub/models--${MODEL_ID//\//--}"

# 用 refs/main 指向的 revision，而不是"排序第一个" snapshot
SNAP=""
for REF in main master; do
  if [ -f "$HUB/refs/$REF" ]; then
    REV="$(cat "$HUB/refs/$REF")"
    if [ -d "$HUB/snapshots/$REV" ]; then SNAP="$HUB/snapshots/$REV"; break; fi
  fi
done
if [ -z "$SNAP" ]; then
  SNAP="$(ls -d "$HUB"/snapshots/*/ 2>/dev/null | head -1 || true)"
fi
[ -n "$SNAP" ] && [ -f "${SNAP}tokenizer_config.json" ] || {
  echo "error: 没找到 tokenizer（期望 $HUB/snapshots/*/tokenizer_config.json）" >&2
  echo "       先跑 scripts/download-weights.sh" >&2
  exit 2
}
echo ">> tokenizer: $SNAP"

OUT_DIR="${OUT_DIR:-$HOME/flash-next}"
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/draft_vocab_build_$(date +%Y%m%d-%H%M%S).log"
echo ">> log: $LOG"

# 语料规模预检：太小的话覆盖率没有统计意义
CHARS=$(du -sb "${CORPUS[@]}" 2>/dev/null | awk '{s+=$1} END {print s+0}')
echo ">> 语料原始字节: $CHARS" | tee -a "$LOG"
if [ "$CHARS" -lt 50000000 ]; then
  echo "   !! 警告：语料 < 50 MB，覆盖率统计噪声可能过大，建议扩充" | tee -a "$LOG"
fi

for N in 65536 131072; do
  OUT="$OUT_DIR/draft_vocab_zh_${N}.npy"
  echo ">> building n=$N -> $OUT" | tee -a "$LOG"
  python3 "$BUILDER" "$SNAP" "$OUT" --n "$N" --corpus "${CORPUS[@]}" 2>&1 | tee -a "$LOG"
  [ -s "$OUT" ] || { echo "error: $OUT 未生成" >&2; exit 1; }
  python3 - "$OUT" <<'PY' | tee -a "$LOG"
import sys, numpy as np
a = np.load(sys.argv[1])
print(f"   verify: {len(a)} ids, dtype={a.dtype}, min={a.min()}, max={a.max()}, sorted={bool((np.diff(a)>0).all())}")
PY
done

echo ">> 完成。挑一个用于启动："
echo "   DRAFT_VOCAB=$OUT_DIR/draft_vocab_zh_65536.npy ./scripts/serve.sh"
echo ">> 逐字比对覆盖率：见 $LOG 里每档的 'corpus token coverage' 行"
echo ">> 验收标准见 docs/03-cn-draft-vocab.md §5"

# --- 宿主机没装 transformers 时的替代跑法 --------------------------------
# sudo docker run --rm \
#   -v "$SNAP":/tok:ro -v "$OUT_DIR":/out -v "${CORPUS[0]}":/corpus:ro \
#   -v "$REPO/tools":/tools:ro \
#   --entrypoint python3 docker.1ms.run/vllm/vllm-openai:qwen38-flash-next \
#   /tools/build_draft_vocab.py /tok /out/draft_vocab_zh_65536.npy --n 65536 --corpus /corpus
