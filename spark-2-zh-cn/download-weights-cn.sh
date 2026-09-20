#!/usr/bin/env bash
# 在墙内下载 Flash-Next 权重的修正版包装脚本。
#
# 为什么需要它 —— 上游 scripts/download-weights.sh 有两个墙内致命坑：
#   1. 它只把 HF_TOKEN 转发进容器，**不转发 HF_ENDPOINT**。于是在宿主机 export 的
#      HF_ENDPOINT 进不了容器，容器里的 hf 去连真 huggingface.co（不可达），报：
#         Error: Local entry not found. [Errno 101] Network is unreachable
#      —— 这个报错极具误导性，看起来像本地缓存或 Xet 的问题，其实是端点没生效。
#   2. 它默认 XET=1 并**显式传** -e HF_HUB_DISABLE_XET=0，会在容器里覆盖你在宿主机
#      export 的同名变量。所以"在宿主机 export HF_HUB_DISABLE_XET=1"对它是无效的。
#
# 本脚本的做法：HF_ENDPOINT=https://hf-mirror.com + 关掉 Xet。
#   hf-mirror 不代理 Xet CAS，对大文件它 302 到 cas-bridge.xethub.hf.co；
#   实测该 302 转发能稳定跑满本条线路的全部带宽，所以关 Xet 没有代价。
#
# 实测（2026-09-20, spark-2）：
#   - 稳定 ~3.3 MB/s，123.62 GiB 约 11 小时
#   - 该速率是这条 LAN 出口带宽的上限，与目标无关：
#       清华 TUNA（国内）3.47 MB/s  |  ModelScope（国内）2.57 MB/s  |  cas-bridge（国际）3.4 MB/s
#     spark-1 同样封顶在 3.1-3.4 MB/s。**换源不会更快，别折腾镜像站。**
#   - 可断点续跑：中断后重跑同一命令即可，部分下载保留为 *.incomplete
#
# 用法：
#   ./download-weights-cn.sh
#   MAX_WORKERS=16 ./download-weights-cn.sh
#   MODEL=RadixArk/Qwen3.8-Flash-Next-NVFP4 ./download-weights-cn.sh   # 老版，三轴全输，仅供对照
set -uo pipefail

MODEL="${MODEL:-nvidia/Qwen3.8-Flash-Next-NVFP4}"
IMAGE="${IMAGE:-qwen38-flash-dgx}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
MAX_WORKERS="${MAX_WORKERS:-8}"
EP="${HF_ENDPOINT:-https://hf-mirror.com}"
EXCLUDE="${EXCLUDE:-}"
LOG="${LOG:-$HOME/flash-next/download.log}"

mkdir -p "$HF_CACHE" "$(dirname "$LOG")"

EXCL_FLAGS=""
for pat in $EXCLUDE; do EXCL_FLAGS="$EXCL_FLAGS --exclude $pat"; done

TOKEN_ARGS=()
[ -n "${HF_TOKEN:-}" ] && TOKEN_ARGS+=(-e HF_TOKEN)
[ -s "$HF_CACHE/token" ] && echo ">> 使用 $HF_CACHE/token 里的 token" \
                         || echo ">> 无 HF_TOKEN：Hub 会限流，gated 仓库会 401（本 checkpoint 非 gated）"

echo ">> model=$MODEL  endpoint=$EP  xet=off  workers=$MAX_WORKERS"
echo ">> 日志: $LOG"
echo ">> 另开 shell 看进度:  du -sh $HF_CACHE/hub/models--*"
echo

sudo docker run --rm --name qwen38-dl \
  -e HF_HOME=/hf \
  -e "HF_ENDPOINT=$EP" \
  -e HF_HUB_DISABLE_XET=1 \
  "${TOKEN_ARGS[@]}" \
  -v "$HF_CACHE:/hf" --entrypoint bash "$IMAGE" \
  -c "hf download '$MODEL' --max-workers $MAX_WORKERS$EXCL_FLAGS" 2>&1 | tee "$LOG"

RC="${PIPESTATUS[0]}"
echo
echo ">> 容器退出码: $RC"
if [ "$RC" != 0 ]; then
  echo ">> 非零退出不代表失败（上游脚本也记录过同一现象：所有分片完整但收尾时 httpx.ReadTimeout）。"
  echo ">> 先复核再决定是否重跑："
fi
L="$HF_CACHE/hub/models--${MODEL//\//--}"
echo "   目标目录: $L"
find "$L/blobs" -type f -name '*.incomplete' -printf '   未完成: %12s  %f\n' 2>/dev/null || true
echo "   已落地合计: $(du -sh "$L" 2>/dev/null | cut -f1)"
echo "   期望总量:   123.62 GiB (nvidia) / 125.96 GiB (RadixArk)"
echo "   → 无 *.incomplete 且总量对得上 = 下载完成。随后跑:  ./flash doctor"
