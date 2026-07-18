# 码流分析工具 — 记忆 / 决策档案 (STREAM_ANALYZER_MEMORY)

> 跨会话单一真相源。位于工程根 `/media/cvitek/yijun.liu01/jmhm`，随仓库迁移。
> 每次开工先读本文件；有新决策/新验证事实就更新它。**目前处于"方案设计完成、尚未写代码"阶段。**

---

## 1. 项目目标

在 `/media/cvitek/yijun.liu01/jmhm` 构建一个功能完整的 **H.264 / H.265 码流分析工具（Web）**，
底层复用仓库内 **JM**(H.264 参考解码器 `JM/bin/ldecod.exe`)、**HM**(H.265 参考解码器/分析器 `HM/bin/TAppDecoderAnalyserStatic`) 与 **ffmpeg/ffprobe**。

五项需求：
1. 设置 ffmpeg 与 JM/HM 路径；输入裸码流或封装视频(自动解封装)。
2. 逐帧/逐秒码率曲线 + 按 GOP 平均码率波动曲线。
3. 码流语法解析：每字段中英文名 + 数值含义，按帧排列。
4. 逐帧 step 预览 + 可开关叠加绘制：**宏块及其所有细分子块(帧内 4×4/8×8/16×16、帧间 16×16/16×8/8×16/8×8 及子分割 8×4/4×8/4×4、Skip/PCM/Direct 等)用不同颜色区分**、参考方向、运动向量、宏块 QP；以及帧内参考关系 / 帧间(与参考帧)参考关系。
5. 逐帧原始数据按 起始码 / Header / 宏块原始数据 分段显示；点击某宏块 → 预览图中高亮该宏块（双向联动）。

---

## 2. 已定架构决策 (2026-07-18)

- **前端 = Web 页面**（`<img>` 解码帧底图 + 多层 `<canvas>` 叠加；ECharts 画曲线；hex 视图）。
- **后端 = Python FastAPI**，调度外部二进制(ffmpeg/ldecod/HM)，解析文本 trace → 统一 JSON。
- **优先级 = H.264 先做**(JM stock 二进制即可跑通 P1–P4)；**HEVC CU 级解析后做**(P5，需重编译 HM 加 `ENC_DEC_TRACE=1`)。
- **统一 JSON 中间层**：H.264(JM)/H.265(HM)/ffmpeg 降级三条解析路径产出同一 schema，前端与解码器解耦。
- **部署**：Docker 一体化(内置 JM/HM/ffmpeg)为主；另备免 Docker 的 `setup.sh`。
- 工程数据放 `workdir/<project_id>/`，可整目录拷贝迁移。

交付分 5 期(每期可独立交付)：
| 期 | 内容 | 工具 | 风险 |
|---|---|---|---|
| P1 | 需求1 路径配置+解封装 · 需求2 码率曲线(逐帧/逐秒/GOP) | ffprobe | ✅ 已完成 |
| P2 | 需求3 H.264 逐帧逐宏块语法解析(JM trace→JSON)+中英文字典 | JM stock | ✅ 已完成 |
| P3 | 需求4 帧预览+**子块级**分割/QP/MV/参考关系叠加 | ffmpeg 出帧 + P2 数据 | 中 |
| P4 | 需求5 原始数据分段 + hex↔预览双向高亮联动 | P2 的 bit offset | 中(CABAC 字节近似) |
| P5 | H.265/HM 全语法(重编译 ENC_DEC_TRACE=1)接同一链路 | HM 重编译 | 中 |

---

## 3. 需求 4 细化：子块分割解析与配色（重点）

**目标**：不止画 16×16 宏块网格，而是把每个宏块内部的**实际预测分割**递归画出来，每类子块一种颜色。

**数据来源（已验证 stock JM trace 即可，无需改解码器）**：
每个宏块从 `trace_dec.txt` 拿到 `mb_type` 数值 + 若为 P/B_8x8 再拿 4 个 `sub_mb_type` 数值 + 16 个 `intra4x4_pred_mode`(或 `intra8x8_pred_mode`) + `transform_size_8x8_flag` + `mvd*_l0/l1` + `ref_idx_l*`。
经 **H.264 标准映射表(Table 7-11 MB types for I，7-13 P，7-14 B；7-17/7-18 sub_mb_type)** 把编号→几何分割，确定性还原。实测确认：`sub_mb_type`(88)、`intra4x4_pred_mode`(1904)、`mvd*_l0`、`ref_idx_l` 均逐条出现在 trace。

**要解析并区分绘制的块类型（配色建议，前端可配置）**：
- 帧内：`I_4x4`(每 4×4 子块，按 9 种预测模式再细分色) / `I_8x8`(每 8×8) / `I_16x16`(整块，含 4 种预测方向) / `I_PCM`(原始像素)。
- 帧间 P：`P_L0_16x16` / `P_16x8` / `P_8x16` / `P_8x8`(再按 4 个 sub 分：`8x8`/`8x4`/`4x8`/`4x4`) / `P_Skip`。
- 帧间 B：`B_Direct_16x16` / `B_L0/L1/Bi_16x16` / `16x8` / `8x16` / `B_8x8`(sub: 8x8/8x4/4x8/4x4，各含 L0/L1/Bi) / `B_Skip`。
- 变换尺寸：`transform_size_8x8_flag` 决定残差是 4×4 还是 8×8 变换，用边框线型(实/虚)叠加区分。
- HEVC(P5)：CTU 64×64 → CU 四叉树(getDepth) → PU 分割(2Nx2N/2NxN/Nx2N/NxN/AMP) → TU(RQT)，配色思路同上。

**渲染方式**：分层 canvas，每类块用不同填充色 + 边框；子块递归绘制(宏块框粗、子块框细)；hover tooltip 显示 `块类型/尺寸/预测模式/QP/MV/ref`。图例面板列出全部块类型颜色。每一叠加层(分割/QP热力/MV/帧内参考方向/帧间参考关系)独立复选框开关。

**可选增强(非必须)**：若要 100% 精确的"解码后最终块结构"(而非从语法推导)，可给 JM `ldecod` 打一个小补丁在 `macroblock.c` decode 完成处 dump 每 MB 的 `mb->b8mode/b8pdir/mb->qp/mv/ref_idx` 到 CSV。列为 P3 的可选项，默认走 stock trace 推导。

---

## 4. 关键已验证技术事实（真实环境 + res/ 样本实测）

- **JM `ldecod.exe` = stock 二进制，`TRACE=1` 已编译进去**(硬编码于 `JM/ldecod/inc/defines.h`)，每次运行自动写 `trace_dec.txt`，含**逐宏块**完整语法：`@bit_offset` + 字段名 + 二进制 + 十进制值；`*********** POC: n MB: m Slice: s Type t **********` 逐 MB 分隔符；`mb_type/sub_mb_type/mb_qp_delta/mvd*_l0/l1/ref_idx_l*/intra4x4_pred_mode/coded_block_pattern/残差系数`。运行方式：`ldecod.exe -p InputFile=x.264 -p OutputFile=dec.yuv`。
- **⚠ JM 源码是 Latin-1 编码**，解析/grep 需 `grep -a` 或 `open(encoding='latin-1')`。
- `@NNNN` 是 **slice/NAL RBSP 内的 bit 偏移**(每 slice 从 @0 重置)；NALU banner 给每 NAL 字节 `len`+`nal_unit_type`+`nal_reference_idc`。二者结合 → 每 NAL/Header/宏块的绝对字节区间(需求5)。
- **HM `TAppDecoderAnalyserStatic` = stock**，给帧级：`POC / [L0 ..][L1 ..] 参考表 / 每 slice QP / 每语法元素 bit 统计`(`RExt__DECODER_DEBUG_BIT_STATISTICS=1` 已编译)。**CU 级全语法需重编译**：改 `HM/build/linux/lib/TLibDecoderAnalyser/makefile` 的 `DEFS` 加 `-DENC_DEC_TRACE=1` 后 `make`(cmake 3.18/gcc 9.4 就绪)。stock `HM/bin/annexBbytecountStatic` 可直接列 HEVC 每 NAL `NumBytesInNALunit`。HM 运行：`TAppDecoderAnalyserStatic -b x.265 -o dec.yuv`。
- **ffmpeg 4.2.7**(`/usr/bin`)：`-bsf:v trace_headers`(h264+hevc，输出 bit位置/字段名/二进制/值)、`codecview=mv=pf+bf+bb`(HEVC 实测出图)、`-flags2 +export_mvs`、`-show_packets`(size + flags，`K`=关键帧=GOP 边界) — 全部实测可用。解封装：`ffmpeg -i in.mp4 -c:v copy -bsf:v hevc_mp4toannexb out.265`(H.264 用 `h264_mp4toannexb`)。
- **`res/*.mp4` 样本全是 HEVC 2560×1440 @15fps**(1234/16.06/4321/watermark_mosac)；另有 `4321.h265`/`watermark_mosac.h265` 裸流。`res/frames.csv`、`res/*.txt` 是先前 ffprobe 输出。
- 现有原型 `VideoAnalyzer/VideoAnalyzer.py`(仅 ffprobe/ffmpeg，MB 解析是占位假逻辑) — **需重构**，可参考其 ffprobe 调用与出图骨架。

---

## 5. 环境快照

- 工作机: Linux(Ubuntu 20.04, Darwin 提示不准确以实际为准)，工程路径 `/media/cvitek/yijun.liu01/jmhm`。
- Python 3.8.10(numpy 有 / **matplotlib 缺**，需 pip 装；后端拟用 FastAPI+uvicorn，出图前端用 ECharts 故 matplotlib 非必须)。
- Node v22.22.2 / npm。cmake 3.18.5 / gcc 9.4 / make 就绪。
- ffmpeg+ffprobe 4.2.7 在 `/usr/bin`。
- 目录：`HM/` `JM/` `res/`(样本) `vc_h264_cmodel/`(另一 H264 cmodel，暂未纳入) `VideoAnalyzer/`(旧原型)。

---

## 6. P1 交付物 (2026-07-18 完成)

代码在 `streamtool/`：
- `app/config.py` — 4 工具路径探测(PATH+仓库默认)/校验(跑 -version)/持久化 `~/.streamtool/config.json`。
- `app/project.py` — `create_project()` 探测 codec、封装文件走 `ffmpeg -bsf hevc/h264_mp4toannexb` 解封装、裸流直接拷贝；数据落 `workdir/<id>/`(stream.264/265 + project.json)。
- `app/bitrate.py` — `analyze_bitrate()` 用 `ffprobe -show_packets` 取 size/pts/flags(K=关键帧=GOP边界)，算逐帧/逐秒分桶/GOP切分平均+波动(σ/峰均比)，缓存 bitrate.json。裸流 pts 常 N/A → 用 帧号/fps 兜底。
- `app/main.py` — FastAPI，接口见 README；挂载 `web/` 为 `/static`。
- `web/` — 纯静态零依赖前端：`index.html`(5标签，后3个P2-P4占位) + `style.css`(深色) + `chart.js`(**自研 Canvas 折线/阶梯图，因环境无网络无法用 ECharts CDN**) + `app.js`。
- `requirements.txt`/`run.sh`(默认端口8731)/`README.md`/`.gitignore`。
- 启动：`cd streamtool && ./run.sh` → `http://127.0.0.1:8731`。
- **已端到端验证**：res/4321.mp4(demux,323帧22GOP)、16.06.mp4(1358帧46GOP)、watermark_mosac.h265(raw,983帧66GOP)、坏路径错误处理、静态资源200 全通过。FastAPI 0.124/uvicorn 0.33 本机已装。

## 6b. P2 交付物 (2026-07-18 完成)

- `app/decoder.py` — `ensure_h264_trace()` 在工程目录 cwd 内跑 `ldecod.exe -p InputFile=.. -p OutputFile=decoded.yuv`，生成 `trace_dec.txt`；按源流 mtime+size 打戳缓存，幂等。仅 H.264(HEVC 抛 DecodeError 待 P5)。
- `app/jm_trace_parser.py`(latin-1) — 解析 NALU banner/字段行/`*** POC/MB ***` 分隔符 → `{frames:[{index,poc,slice_type,frame_num,nals:[{fields}],macroblocks:[{fields,residuals}]}], nal_index}`。**关键坑**：JM 有"预读下一个 slice header"行为(slice NAL 比其宏块提前一拍)，用 slice_fifo(FIFO bundle) 而非就近归属解决。CAVLC 系数编码中间量(coeff_token/level 等，`_COEFF_MARKERS`)路由到 residuals 桶，保持语法字段干净。
- `app/data/syntax_dict_h264.json` — H.264 字段英文名→{zh,desc,clause}；覆盖 SPS/VUI/PPS/SliceHeader/MB 全部字段。实测 MB/NAL 字段中文覆盖 100%。
- `app/syntax.py` — `syntax_overview()`(帧列表) + `frame_syntax(idx, include_mbs)`(单帧语法树)；`_normalize_field_name` 归一 JM 通用名(mvd_l→mvd_l0 等)；两级磁盘缓存(trace + parsed)。
- `app/main.py` — `GET /api/project/{id}/syntax`、`GET /api/project/{id}/frame/{index}/syntax?mbs=`。
- 前端 `web/` — ③标签启用(仅 H.264)：左侧帧列表(I/P/B 色块+POC+fnum)，右侧 NAL/MB 分组折叠语法树(英文名|中文名|bit|二进制|数值|含义|章节)，字段搜索(中/英,命中自动展开),显示宏块开关。
- **已验证**：CABAC/CAVLC 均可解析；10帧小流解码+解析 0.26s(缓存 0.036s)；50帧 CIF(396MB/帧) 5.8s；HEVC 正确拒绝(400+中文提示);mbs=false 生效。
- **注意打包**：`app/data/` 必须随仓库发布(已确认不被 .gitignore)。JM 源码 Latin-1。

## 7. 待办 / 下一步
- [ ] 等后台调研 agent 补齐 HM `TComDataCU` 访问器(getQP/getPredictionMode/getPartitionSize/getCUMvField/getInterDir/getDepth) 与 HM Analyser CLI 细节 → 供 P5 使用(不阻塞 P2-P4)。
- [ ] 决定统一 JSON schema 的最终字段(在 P2 落地时冻结)。
- [ ] 前端标签③④⑤ 目前是占位，随 P2-P4 填充。
