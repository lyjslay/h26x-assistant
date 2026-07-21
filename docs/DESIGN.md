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
| P3 | 需求4 帧预览+**子块级**分割/QP/MV/参考关系叠加 | ffmpeg 出帧 + P2 数据 | ✅ 已完成 |
| P4 | 需求5 原始数据分段 + hex↔预览双向高亮联动 | P2 的 bit offset | ✅ 已完成 |
| P5 | H.265/HM 全语法(重编译 ENC_DEC_TRACE=1)接同一链路 | HM 重编译 | ✅ 已完成 |

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

## 6c. P3 交付物 (2026-07-18 完成)

- `app/preview.py` — `build_frame_map()` 建立**显示序↔解码序↔POC**映射。**关键事实(实测)**：JM 写 decoded.yuv 与 ffmpeg 出图都是**显示序(POC排序)**，而 trace 是**解码序**；多 GOP 时 POC 在每个 IDR 重置，故映射需**按 IDR 分段、段内 POC 升序**。`ensure_frame_png()` 用 `ffmpeg select=eq(n,idx)` 惰性抽显示序帧。
- `app/mb_partition.py` — 由 mb_type/sub_mb_type/transform_size_8x8_flag/intra模式 经 **H.264 Table 7-11~7-18** 确定性还原每个子块 x/y/w/h/kind/pred。**关键坑**：JM trace 的 `mb_skip_flag` 数值**不能**当"是否跳过"——真正 skip 的宏块根本不解析 mb_type，故以 **mb_type 缺失**判 skip。子块拼接零间隙已验证(每 MB 恰好 256px²)。kind 分类：intra_4x4/8x8/16x16/pcm、inter_16x16/16x8/8x16/8x8/8x4/4x8/4x4、skip/direct。
- `app/overlay.py` — `build_frame_overlay()` 组织每帧 blocks(子块配色+QP+MV) + `build_reference_graph()`。**关键坑**：QP 累加是 **mb_qp_delta 模 52 环绕**(`(qp+dq+52)%52`, spec 7.4.5)，不是 clamp。sliceQP=26+pic_init_qp_minus26+slice_qp_delta。MV 用 mvd 近似(真实 MV 需预测重建,列 P4+增强)；参考关系用 IDR 分段内 POC 邻近近似。
- `app/main.py` — `/framemap`、`/frame/{disp}/image`(PNG)、`/frame/{dec}/overlay`、`/refgraph`。
- 前端 `web/preview.js`(独立文件) — ④标签：帧 step 控件+缩放，底图<canvas>+叠加<canvas>分层，5 图层复选框(划分/QP热力/MV/帧内方向/参考关系)，块类型配色图例，帧信息面板，hover 子块 tooltip。底图按显示序、叠加按解码序(经 framemap 对应)。
- **注意**：overlay 用 ffprobe 报告的显示尺寸(如1080)，mb_grid 用 ceil(h/16)(如68行覆盖编码1088)，底图与叠加对齐已验证(352x288 与 1920x1080 均 match)。app **不依赖 PIL**(仅测试用)。
- **已验证**：I/P/B 子块分布合理；坐标零越界；QP 修复后范围合理；图像各显示帧 200；HD 1080p 裁剪对齐正确；全回归通过。

## 6d. P4 交付物 (2026-07-18 完成)

- `app/nal_bytes.py` — `scan_nals()` 扫 Annex-B 起始码(00 00 01 / 00 00 00 01)，给每 NAL 的 sc_start/payload_start/nal_end 字节精确区间。顺序与 trace 的 nal_index 一致。
- `app/rawmap.py` — `build_frame_rawmap()` 每帧分段：起始码/NAL头/SPS-PPS/SEI/片头/各宏块。**字节精确**部分=起始码+NAL头+参数集载荷(来自字节扫描)。宏块字节区间=用 trace 每 MB 的**相对 bit**(`mb_i.first_bit - mb_0.first_bit`)按比例映射到 slice 载荷字节区间。**关键坑/事实**：
  - trace `@bit` 是**全局累积计数**，不是每 NAL 重置；且有 P2 的读前一拍现象。
  - CAVLC：@bit 是真实比特位置 → 宏块字节**精确**(实测 slice bit 跨度/8 ≈ NAL len 字节)。
  - CABAC：@bit 是**符号计数**(算术编码不按 bit 对齐) → 宏块字节**近似**，rawmap 标 `mb_mapping=approx`、每 MB 段 `approx=true`。判 CAVLC/CABAC 看 PPS `entropy_coding_mode_flag`。
  - 片头字节数由 trace SH 字段 bit 跨度估算；MB 段实测零间隙、连续覆盖到 nal_end。
- `app/main.py` — `/frame/{d}/rawmap?hexdata=&max_bytes=`：返回分段 + hex 字符串(默认上限 256KiB，超出截断标注)。
- 前端 `web/rawview.js`(独立文件) — ⑤标签：逐帧(解码序)hex 视图，每字节按分段着色(起始码/NAL头/参数集/SEI/片头/宏块交替深浅)，分段图例+分段列表。**双向联动**：hex 点宏块→高亮+滚动+调 `Preview.highlightMb(decodeIndex,mb)` 切帧预览高亮；预览点宏块→调 `RawView.showMbFromPreview` 反向。共享 mb_index；overlay 与 rawmap 的宏块索引集合实测完全一致。
- preview.js 增 `highlightMb()`(经 framemap.decode_to_display 切到对应显示帧再高亮) 与预览 click→通知 RawView。
- **已验证**：起始码字节=00000001；CAVLC exact/CABAC approx 标注正确；overlay↔rawmap 宏块索引一致(99/99)；全回归通过。

## 6e. P5 交付物 (2026-07-19 完成) —— HEVC 全链路

**HM 重编译(唯一编译步)**：`streamtool/hm_patch/build_hm_analyser.sh <HM根>` 幂等完成：
- 打 `TDecCu_cu_dump.patch`(在 `xFinishDecodeCU` 每叶子 CU dump 一行到 `cu_dump.csv`)。
- 给 3 个 makefile DEFS 加 `-DENC_DEC_TRACE=1 -DHM_CU_DUMP=1`。**关键坑**：`g_hTrace/g_nSymbolCounter` 在 `TLibCommon/TComRom.cpp` 的 `#if ENC_DEC_TRACE` 下，故 **TLibCommon 也必须重编**(只加 ENC_DEC_TRACE)，否则链接报 undefined reference。重编顺序 TLibCommon→TLibDecoderAnalyser→TAppDecoderAnalyser。
- cu_dump 列：`poc,ctu,x,y,size,depth,predMode(0inter/1intra/2none),partSize(0-7),qp,intraDirY,interDir(1L0/2L1/3Bi),mvL0x/y,refL0,mvL1x/y,refL1`。

**三份数据源**(HM 一次运行 `-b in.265 -o out.yuv` 全产出，落工程 cwd)：
- `hm_stdout.txt`：每 POC 摘要 `POC n (X-SLICE, QP q) [L0 ..][L1 ..]` → 帧列表(解码序)+**精确参考列表**(比 H.264 的近似强)。
- `TraceDec.txt`：VPS/SPS/PPS/Slice 头逐字段(格式 `<bit> <名> <descriptor> : <值>`，段标题 `===== X =====`)。HM 无 CU 结构 trace(CABAC)。
- `cu_dump.csv`：逐叶子 CU 精确几何/QP/预测/MV。

**代码**：`hm_decoder.py`(驱动+stdout解析) `hm_trace_parser.py`(头) `hm_cu.py`(CU) `hevc_syntax.py` `hevc_overlay.py` `hevc_rawmap.py` + `data/syntax_dict_h265.json`。main.py 按 codec 分派(`_syntax_mod/_overlay_mod/_rawmap_mod`)。前端 preview.js/rawview.js/app.js 放开 h264-only 限制、加 HEVC 配色与说明。

**关键坑/事实**：
- **cu_dump 必须按解码序切段，不能按 POC 分组**！多 GOP 时 POC 每个 IDR 重置为 0，按值分组会把 N 个 POC=0 的帧合并(实测 1440p 帧0 变 539K CU)。`hm_cu.parse_cu_dump_segments` 按相邻 POC 变化切段(CU 行按解码序连续写)。
- HEVC slice_type: **0=B 1=P 2=I**(与 H.264 相反)。
- HEVC NAL 头 **2 字节**，type=(byte0>>1)&0x3F(H.264 是 1 字节 &0x1F)。
- HEVC 原始数据只到 **NAL 级字节精确**(无 CU 级字节映射：CABAC + cu_dump 无 bit 位置)，故 ⑤ 页 HEVC 无 CU↔预览联动，已标注。
- HEVC 帧图像仍用 ffmpeg 从裸流按显示序抽(preview.py framemap 已支持 hevc 分支，按 IDR 分段+POC 排序)。
- **已验证**：小流(6帧)+真实 1440p(282帧,22 IDR) 全链路;PU 拼满 CU 零间隙;两 codec 所有端点 200;QP/参考列表对齐 HM 日志。

## 6f. 后续增强 (2026-07-21): 后台解码进度 + HEVC TU/SAO

**后台解码 + 进度**：
- `app/jobs.py` — `start_decode()` 后台线程跑解码，`_stream_run` 逐行读解码器 stdout，命中帧模式(JM `^\d+\( X \)` / HM `^POC n TId`)则 +1 进度。`get_status()` 返回 `{state(idle/pending/running/done/error),done,total,message,error}`。总帧数用 `ffprobe -count_packets`。每工程一把 `project.get_decode_lock` 串行化，防重复解码。已解码则 `_already_decoded` 直接 done。
- `app/main.py` — `POST /decode`(启动/复用) + `GET /decode/status`(轮询)。
- 前端 `app.js` `window.App.ensureDecoded(pid, onProgress)`(启动+轮询,done resolve) + `progressText`；③④⑤页进入前先 ensureDecoded 显示进度。**坑**：JM 帧计数行数可能比 ffprobe 包数略少(done 48/total 50)，无碍,以 state=done 为准。

**HEVC TU/RQT + SAO 叠加**：
- HM 补丁(`TDecCu_cu_dump.patch` 已更新，157行)新增两处 dump：`hmTuDumpLeaf`(递归 RQT 叶子 TU 矩形→`tu_dump.csv`: poc,x,y,size,cu_x,cu_y,cu_size；用 `getTransformIdx` 判 TU 深度,tuSize>4 才递归) + `hmSaoDumpCtu`(在 `decodeCtu` 末尾，每 CTU 各分量 SAO→`sao_dump.csv`: poc,ctu,x,y,size,comp,mode,typeIdc,typeAux；经 `getPicSym()->getSAOBlkParam()[ctu][comp]`)。**重编译**：只 TDecCu.r.o 变，`make -C lib/TLibDecoderAnalyser release` + app 链接即可(makefile 宏已在)。
- `app/hm_cu.py` — `rows_for_decode_index`(通用按 POC 变化切段，同 CU 分段坑) 供 TU/SAO。
- `app/hevc_overlay.py` — overlay 加 `tu`(矩形列表) + `sao`(逐 CTU 聚合，取亮度 comp=0 主类型；`_sao_kind`: mode 0off/1new/2merge，new typeIdc 0-3=EO 各角度/4=BO)。
- 前端 `preview.js` — 加 `drawTuLayer`(红虚线网格) `drawSaoLayer`(SAO 类型着色)；`lyTu/lySao` 复选框(`.hevc-only` 仅 HEVC 显示)；SAO 图例；legend 按 codec 过滤块类型(HEVC 只列 `*_cu`)。
- **验证**：TU 527/帧、SAO 8 CTU 分类正确；H.264 overlay 无 tu/sao 键(前端 return 容错)；两 codec 全端点 200。

## 7. 待办 / 下一步
- [x] **五项需求 H.264 + H.265 全部完成(P1-P5)**。
- [ ] 后续可选增强：H.264 MV 精确重建(mvd→MV 预测)；HEVC TU/RQT 叠加层、SAO/去块可视化；HEVC 原始数据若要 CU 级需再给 HM 打字节位置补丁；多 slice/Tile/WPP 更细处理；性能(1440p 解码~98s，可加进度反馈/后台任务)。
- [ ] Docker 镜像内自动跑 hm_patch 构建脚本(当前 Dockerfile 示例未含)。
