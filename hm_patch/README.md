# HM 分析器补丁（HEVC CU 级分析所需）

streamtool 的 **P5（HEVC 分析）** 需要一个**重编译版**的 HM `TAppDecoderAnalyser`，
在官方 HM 源码上启用两项能力：

| 编译宏 / 补丁 | 产出 | 用途 |
|---|---|---|
| `-DENC_DEC_TRACE=1` | `TraceDec.txt`（VPS/SPS/PPS/Slice 头逐字段） | ③ 语法解析页 |
| `-DHM_CU_DUMP=1` + `TDecCu.cpp` 补丁 | `cu_dump.csv`（逐 CU 几何/深度/QP/预测/MV/ref） | ④ 帧预览叠加、CU 语法 |

> 官方 stock 的 `TAppDecoderAnalyserStatic` **不含**这两者，用它跑 HEVC 时
> streamtool 会提示“未生成 cu_dump.csv，请使用本项目重编译版”。

## 一键构建

```bash
./build_hm_analyser.sh /path/to/HM      # HM 根目录，需含 build/linux
```

脚本会（幂等）：
1. 给 `source/Lib/TLibDecoder/TDecCu.cpp` 打 `TDecCu_cu_dump.patch`（逐叶子 CU dump）；
2. 给 `TLibDecoderAnalyser`、`TAppDecoderAnalyser`、`TLibCommon` 三个 makefile 的 `DEFS`
   追加 `-DENC_DEC_TRACE=1 -DHM_CU_DUMP=1`（TLibCommon 只加 TRACE，用于提供 `g_hTrace` 符号）；
3. 依次重编 TLibCommon → TLibDecoderAnalyser → TAppDecoderAnalyser；
4. 校验产出的 `bin/TAppDecoderAnalyserStatic` 含 CU dump 能力。

构建完成后，在 streamtool 设置页把 `hm_analyser` 指向该 `bin/TAppDecoderAnalyserStatic` 即可。

## 补丁内容

`TDecCu_cu_dump.patch` 在 `TDecCu::xFinishDecodeCU`（每个叶子 CU 解码完成处）插入
一行 CSV 输出，字段：

```
poc,ctu,x,y,size,depth,predMode,partSize,qp,intraDirY,interDir,mvL0x,mvL0y,refL0,mvL1x,mvL1y,refL1
```

- `predMode`: 0=inter, 1=intra, 2=none
- `partSize`: HEVC PartSize 枚举（0=2Nx2N …7=nRx2N）
- `interDir`: 1=L0, 2=L1, 3=Bi

补丁仅在 `HM_CU_DUMP` 宏开启时生效，不影响官方其他构建。

## 兼容性

- 基于 HM-16.x（本项目验证于随仓库的 HM 源码树，Decoder Version 16.8）。
- 仅改动 Linux makefile 构建；Windows(vcproj) 用户可自行在项目属性的预处理器定义中加同名宏。
