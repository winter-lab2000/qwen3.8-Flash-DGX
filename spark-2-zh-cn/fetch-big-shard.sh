#!/usr/bin/env bash
# 下载单个"超大分片"，用「分段 + 每段重新取签名 URL」绕开签名有效期。
#
# 为什么必须这样：
#   - hf-mirror 对仓库里的大文件不做镜像，而是 302 到 cas-bridge.xethub.hf.co，
#     那条签名 URL 的有效期是 3600 秒（X-Amz-Expires=3600）。
#   - 本 LAN 实测约 3.3 MB/s，所以 >12 GiB 的文件单次下载**必然**超过有效期而中断。
#       9.32 GiB 分片 ≈ 48 分钟 → 卡在 1 小时内，全部成功
#       50.03 GiB 分片 ≈ 4.5 小时 → 必然超时
#   - 纯 HTTPS 路径还会被直接拒绝：
#       "The file is too large to be downloaded using the regular download method"
#   - Xet 路径在本 LAN 也不通：客户端会去连真·CAS 服务 cas-server.xethub.hf.co，
#     匿名请求返回 401 Unauthorized。
#   => 唯一可行：切段，每段都重新取一次签名，每段耗时远小于 3600 秒。
#
# 可反复运行：从已下字节处继续，不会重头来。
#
# 用法：
#   ./fetch-big-shard.sh                 # 正式跑
#   CHUNK=104857600 MAX_SECONDS=150 ./fetch-big-shard.sh   # 短测
set -uo pipefail

REPO="${REPO:-nvidia/Qwen3.8-Flash-Next-NVFP4}"
FILE="${FILE:-model-fp8-mtp-ple.safetensors}"
TOTAL="${TOTAL:-53717551730}"
EXPECT_SHA="${EXPECT_SHA:-3525520c8602d850003eb1960aec0b64291dae33b83f8b65dd639a451df78823}"
EP="${EP:-https://hf-mirror.com}"
CHUNK="${CHUNK:-4294967296}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
MAX_SECONDS="${MAX_SECONDS:-0}"
LOG="${LOG:-$HOME/flash-next/fetch-big-shard.log}"
OUT="${OUT:-$HOME/flash-next/$FILE.part}"

LINK="$HF_CACHE/hub/models--${REPO//\//--}"
BLOBS="$LINK/blobs"
SNAPS="$LINK/snapshots"
BASE="$EP/$REPO/resolve/main/$FILE"
START_TS=$(date +%s)

mkdir -p "$(dirname "$OUT")" "$(dirname "$LOG")" "$BLOBS" "$SNAPS"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
pct() { awk -v c="$1" -v t="$TOTAL" 'BEGIN{printf "%.1f%%", c*100/t}'; }
gib() { awk -v c="$1" 'BEGIN{printf "%.2f GiB", c/2^30}'; }

# 已装好就跳过
if [ -f "$BLOBS/$EXPECT_SHA" ] && [ "$(stat -c%s "$BLOBS/$EXPECT_SHA")" = "$TOTAL" ]; then
  log "blob 已存在且大小正确，无需下载: $BLOBS/$EXPECT_SHA"
  exit 0
fi

log "文件 $FILE  共 $TOTAL B ($(gib "$TOTAL"))  段大小 $CHUNK B  输出 $OUT"

probe=$(curl -sS -o /dev/null -w '%{redirect_url}' --max-time 30 "$BASE")
if [ -z "$probe" ]; then log "!! 取不到签名 URL，先确认 $EP 可达"; exit 1; fi
log "签名 URL 主机: $(echo "$probe" | sed -E 's#^(https://[^/]+).*#\1#')"

cs=0
while [ "$cs" -lt "$TOTAL" ]; do
  ce=$(( cs + CHUNK - 1 )); [ "$ce" -ge "$TOTAL" ] && ce=$(( TOTAL - 1 ))
  target=$(( ce + 1 ))
  tries=0
  while :; do
    cur=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
    [ "$cur" -ge "$target" ] && break
    tries=$(( tries + 1 ))
    if [ "$tries" -gt 10 ]; then log "!! 段 $((cs/CHUNK+1)) 重试超限，退出（已下进度保留）"; exit 2; fi

    U=$(curl -sS -o /dev/null -w '%{redirect_url}' --max-time 30 "$BASE")
    if [ -z "$U" ]; then log "   取签名失败，5 秒后重试"; sleep 5; continue; fi

    want=$(( ce - cur + 1 ))
    log "  $(pct "$cur")  $(gib "$cur")  续传 ${cur}-${ce} (${want} B)"
    before=$cur
    curl -fL --retry 2 --retry-delay 5 --max-time 3300 -r "${cur}-${ce}" "$U" >> "$OUT"
    rc=$?
    after=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
    delta=$(( after - before ))
    if [ "$delta" -gt "$want" ]; then
      log "!! 服务端忽略了 Range（多收 $delta > $want 字节），中止以免文件错位"; exit 5
    fi
    [ "$rc" != 0 ] && log "   curl 退出码 $rc（中断，下一轮从 $after 继续）"

    if [ "$MAX_SECONDS" != "0" ] && [ $(( $(date +%s) - START_TS )) -ge "$MAX_SECONDS" ]; then
      log "  达到 MAX_SECONDS=$MAX_SECONDS，主动退出（进度已保留，重跑续传）"; exit 0
    fi
  done
  cs=$(( ce + 1 ))
done

got=$(stat -c%s "$OUT")
log "下载完毕：$got B"
if [ "$got" != "$TOTAL" ]; then log "!! 大小不符：期望 $TOTAL 实得 $got"; exit 3; fi

log "校验 sha256（比 S3 校验和更可信的端到端验证）..."
real=$(sha256sum "$OUT" | cut -d' ' -f1)
if [ "$real" != "$EXPECT_SHA" ]; then
  log "!! sha256 不符，文件不可用"; log "   期望 $EXPECT_SHA"; log "   实得 $real"; exit 4
fi
log "sha256 正确 ✓"

log "装入 HF 缓存（blob 名 = LFS sha256）..."
# 下载通常是由 root 容器发起的，缓存目录可能归 root:root，于是 mv/ln 报"权限不够"。
# 这里先确保可写，避免 hash 都校验过了却在最后一步白费（已经吃过一次这个亏）。
if [ ! -w "$BLOBS" ]; then
  log "  $BLOBS 不可写（容器以 root 创建过），用 sudo 把所有权交回 $(id -un) ..."
  sudo chown -R "$(id -u):$(id -g)" "$HF_CACHE" || log "  !! chown 失败，请手工执行 sudo chown -R $(id -u):$(id -g) $HF_CACHE"
fi
mv -f "$OUT" "$BLOBS/$EXPECT_SHA"
n=0
for d in "$SNAPS"/*/; do
  [ -d "$d" ] || continue
  ln -sf "../../blobs/$EXPECT_SHA" "$d/$FILE"
  log "  已建链接 $d$FILE"
  n=$(( n + 1 ))
done
log "完成（snapshot 数 $n）。下一步：cd ~/flash-next/qwen3.8-Flash-DGX && ./flash doctor"
