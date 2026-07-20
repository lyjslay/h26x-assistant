#!/usr/bin/env bash
# 构建**带 trace + CU dump** 的 HM 分析器(供 streamtool P5 HEVC 分析)。
#
# 作用：在官方 HM 源码基础上启用两项能力，重编译 TAppDecoderAnalyser：
#   1. -DENC_DEC_TRACE=1  → 解码时输出 TraceDec.txt(VPS/SPS/PPS/Slice 头逐字段) —— 供语法页
#   2. -DHM_CU_DUMP=1 + TDecCu.cpp 补丁 → 输出 cu_dump.csv(逐 CU 几何/QP/预测/MV) —— 供预览叠加
#
# 用法:  ./build_hm_analyser.sh /path/to/HM
#   HM 目录需为可编译的 HM 源码树(含 build/linux)。
set -e

HM_DIR="${1:-}"
if [ -z "$HM_DIR" ] || [ ! -d "$HM_DIR/build/linux" ]; then
  echo "用法: $0 <HM根目录>   (需含 build/linux)"
  exit 1
fi
HM_DIR="$(cd "$HM_DIR" && pwd)"
PATCH_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> HM 目录: $HM_DIR"

# 1) 打 TDecCu.cpp 的 CU dump 补丁(若尚未打)
CU_SRC="$HM_DIR/source/Lib/TLibDecoder/TDecCu.cpp"
if grep -q "HM_CU_DUMP" "$CU_SRC"; then
  echo "==> TDecCu.cpp 已含 CU dump 补丁，跳过"
else
  echo "==> 应用 TDecCu.cpp CU dump 补丁"
  patch -p1 -d "$HM_DIR" < "$PATCH_DIR/TDecCu_cu_dump.patch"
fi

# 2) 给三个 makefile 的 DEFS 追加编译宏(幂等)
add_defs() {
  local mk="$1"
  if ! grep -q "ENC_DEC_TRACE=1" "$mk"; then
    sed -i 's|\(^DEFS[[:space:]]*=.*RExt__DECODER_DEBUG_BIT_STATISTICS=1\)|\1 -DENC_DEC_TRACE=1 -DHM_CU_DUMP=1|' "$mk"
    echo "   patched DEFS: $mk"
  else
    echo "   DEFS 已含宏: $mk"
  fi
}
add_defs "$HM_DIR/build/linux/lib/TLibDecoderAnalyser/makefile"
add_defs "$HM_DIR/build/linux/app/TAppDecoderAnalyser/makefile"

# TLibCommon 需重编以提供 g_hTrace/g_nSymbolCounter 符号
COMMON_MK="$HM_DIR/build/linux/lib/TLibCommon/makefile"
if ! grep -q "ENC_DEC_TRACE=1" "$COMMON_MK"; then
  sed -i 's|\(^DEFS[[:space:]]*=[[:space:]]*-DMSYS_LINUX\)$|\1 -DENC_DEC_TRACE=1|' "$COMMON_MK"
  echo "   patched DEFS: $COMMON_MK"
fi

# 3) 重编译(清理相关 obj 确保带新宏)
cd "$HM_DIR/build/linux"
echo "==> 重编 TLibCommon"
rm -f lib/TLibCommon/objects/*.r.o
make -C lib/TLibCommon release >/dev/null
echo "==> 重编 TLibDecoderAnalyser"
rm -f lib/TLibDecoderAnalyser/objects/*.r.o
make -C lib/TLibDecoderAnalyser release >/dev/null
echo "==> 链接 TAppDecoderAnalyser"
rm -f app/TAppDecoderAnalyser/objects/*.r.o
make -C app/TAppDecoderAnalyser release >/dev/null

BIN="$HM_DIR/bin/TAppDecoderAnalyserStatic"
if [ -x "$BIN" ] && strings "$BIN" | grep -q "cu_dump.csv"; then
  echo "==> 完成: $BIN (含 TRACE + CU dump)"
else
  echo "!! 构建异常: 未在二进制中发现 cu_dump.csv 标记" >&2
  exit 1
fi
