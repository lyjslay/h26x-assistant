# h26x-assistant · H.264/H.265 码流分析工具

基于 **JM**(H.264/AVC 官方参考解码器)、**HM**(H.265/HEVC 官方参考解码器/分析器) 与 **FFmpeg**
的图形化码流分析工具。后端 Python(FastAPI)，前端纯 Web(原生 Canvas，**零第三方 JS 依赖，完全离线可用**)。

在浏览器里查看码率曲线、逐帧逐宏块语法、预测块划分/运动矢量/参考关系叠加、原始字节分段——
把参考解码器的深度信息可视化出来，用于码流调试、教学与算法分析。

> **当前进度：P5 已完成（全部五项需求 H.264/H.265 均已实现）** —— 在 P1~P4 基础上，
> 新增 **H.265/HEVC 全链路**：语法解析（VPS/SPS/PPS/Slice + CU 级）、帧预览叠加
> （CTU 四叉树 / CU / PU 分割分类配色、MV、QP、参考关系）、原始数据 NAL 级分段。
> HEVC 依赖一个**重编译版 HM 分析器**（见 `hm_patch/`，启用 CU dump + trace）。

---

## 目录

- [功能](#功能)
- [运行环境](#运行环境)
- [依赖：FFmpeg 与 JM/HM 参考解码器](#依赖ffmpeg-与-jmhm-参考解码器)
- [安装与启动](#安装与启动)
- [使用方法](#使用方法)
- [配置说明](#配置说明)
- [REST 接口](#rest-接口)
- [Docker 部署](#docker-部署可选)
- [目录结构](#目录结构)
- [路线图](#路线图)
- [常见问题](#常见问题)

---

## 功能

| # | 需求 | 状态 |
|---|---|---|
| 1 | 设置 FFmpeg / JM / HM 路径；输入裸码流或封装视频(自动解封装为 Annex-B) | ✅ P1 |
| 2 | 逐帧 / 逐秒码率曲线 + 按 GOP 平均码率与波动指标 | ✅ P1 |
| 3 | 码流语法解析：每字段中英文名 + 数值含义，按帧排列 | ✅ P2 (H.264) · P5 (HEVC) |
| 4 | 逐帧预览 + 叠加绘制：宏块及各类细分子块配色、MV、参考方向、QP、帧内/帧间参考关系 | ✅ P3 (H.264) · P5 (HEVC) |
| 5 | 原始数据按起始码/Header/宏块分段；点击宏块在预览图高亮(双向联动) | ✅ P4 (H.264) · P5 (HEVC NAL级) |
| — | H.265/HEVC CU 级深度解析（CTU 四叉树/CU/PU/QP/MV/参考 + TU/RQT + SAO） | ✅ P5 |
| — | 所有码流解码进度反馈 / 后台任务（大流不阻塞，实时进度） | ✅ |

---

## 运行环境

- **操作系统**：Linux / macOS（Windows 建议用 WSL2 或 Docker）。开发验证于 Ubuntu 20.04。
- **Python** ≥ 3.8（开发用 3.8.10）。
- **FFmpeg / ffprobe** ≥ 4.2（需带 `trace_headers` bsf 与 `codecview` 滤镜，主流发行版自带的均满足）。
- **浏览器**：任意现代浏览器（Chrome / Edge / Firefox）。前端无需联网、无需构建。
- **磁盘**：每个分析工程会在 `workdir/` 下缓存解封装裸流与中间数据。

Python 依赖（见 `requirements.txt`）：`fastapi` · `uvicorn` · `pydantic`。

---

## 依赖：FFmpeg 与 JM/HM 参考解码器

本工具本身**不包含** FFmpeg 与 JM/HM 的二进制，需在本机提供。三者路径可在界面「设置」页填写，
工具也会自动探测（系统 `PATH`、环境变量 `JMHM_ROOT`、以及上级目录中的 `JM/bin`、`HM/bin`）。

### 1) FFmpeg（必需，P1 起即用）

```bash
# Ubuntu / Debian
sudo apt install ffmpeg
# macOS
brew install ffmpeg
# 验证
ffmpeg -version && ffprobe -version
```

### 2) JM —— H.264 参考解码器（P2 起需要）

JM 是 ITU-T/ISO 官方 H.264 参考软件。获取并编译 `ldecod`：

```bash
# 官方下载页：https://iphome.hhi.de/suehring/  （JM reference software）
# 解压后在源码根目录：
sh unixprep.sh          # 首次：处理换行、建 obj 目录
make -C ldecod          # 生成 bin/ldecod.exe
./bin/ldecod.exe -v     # 验证，应打印 "JM 19 (FRExt) ..."
```

> 说明：JM 解码器的 `TRACE` 在源码 `ldecod/inc/defines.h` 中默认开启（`#define TRACE 1`），
> 运行时会自动生成含**逐宏块语法**的 `trace_dec.txt`，这是 P2 语法解析的数据来源，无需额外改动。

### 3) HM —— H.265 参考解码器/分析器（HEVC 分析需要）

HEVC 分析需要一个**重编译版** HM 分析器：在官方 HM 源码上启用 `ENC_DEC_TRACE=1`
（输出头部 trace）+ `HM_CU_DUMP=1` 与一处 `TDecCu.cpp` 补丁（输出逐 CU 数据）。
仓库内已附一键构建脚本：

```bash
# 官方仓库：https://vcgit.hhi.fraunhofer.de/jvet/HM
./hm_patch/build_hm_analyser.sh /path/to/HM   # 打补丁 + 加编译宏 + 重编译(幂等)
# 产出 HM/bin/TAppDecoderAnalyserStatic，把它填到设置页的 hm_analyser
```

细节见 [`hm_patch/README.md`](hm_patch/README.md)。
> 官方 stock 分析器不含 CU dump；用它跑 HEVC 时工具会提示需换重编译版。
> 该步骤仅 HEVC 分析用到；纯 H.264 使用不需要 HM。

### 路径自动发现

若你把本工具与 `JM/`、`HM/` 放在同一父目录（即 `父目录/{JM,HM,streamtool}` 或
`父目录/{JM,HM}` 且工具在其下），启动时会自动找到 `JM/bin/ldecod.exe` 与 `HM/bin/TAppDecoderAnalyserStatic`。
否则用环境变量指定根目录：

```bash
export JMHM_ROOT=/path/to/dir/containing/JM/and/HM
```

或直接在界面「设置」页填绝对路径（优先级最高，会持久化）。

---

## 安装与启动

```bash
git clone git@github.com:lyjslay/h26x-assistant.git
cd h26x-assistant

# 建议使用虚拟环境（可选）
python3 -m venv venv && source venv/bin/activate

pip install -r requirements.txt

# 启动（默认 127.0.0.1:8731）
./run.sh
# 指定端口 / 对外监听：
HOST=0.0.0.0 ./run.sh 8000
```

浏览器打开 `http://127.0.0.1:8731`。

> `run.sh` 会自动激活同目录 `venv/`（若存在）并做依赖自检。也可直接：
> `python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8731`

---

## 使用方法

1. **① 设置与输入**
   - 打开后四个工具路径会自动探测：**绿灯 = 可用，红灯 = 不可用**（悬停看版本/错误）。
     只有 FFmpeg 是 P1 必需，JM/HM 可后续再配。
   - 修改路径后点「保存并校验」，配置持久化到 `~/.streamtool/config.json`。
   - 点「📁 选择本机文件…」按钮，从本机选择要分析的视频/码流文件（默认方式，浏览器上传，带进度条）。
     - 裸码流（`.264/.h264/.avc/.265/.h265/.hevc/.bin`）直接载入；
     - 封装文件（`.mp4/.mkv/.ts/.flv…`）自动用 FFmpeg 解封装为 Annex-B。
   - 大文件若不想上传：展开「▸ 高级」，直接填**服务器本机绝对路径**加载（免拷贝，适合工具与文件同机）。
   - 下方显示识别到的编码 / 分辨率 / 帧率 / Profile 等。

2. **② 码率分析**
   - 「逐帧 / 逐秒」切换；勾选「GOP 平均阶梯线」叠加各 GOP 平均码率；「I/关键帧标记」高亮关键帧。
   - 顶部卡片给出总码率、GOP 数、峰值帧、**GOP 波动 σ、峰均比**等指标。
   - 鼠标悬停曲线查看每帧/每秒详情。

3. **③ 语法解析**（H.264）
   - 加载 H.264 码流后自动可用（首次会调用 JM 解码器生成 trace，稍慢；之后缓存）。
   - 左侧帧列表（I/P/B + POC + frame_num），点击查看该帧语法树。
   - 右侧按 **NAL → 字段 / 宏块 → 字段** 分组折叠展示，每字段列：英文名 / 中文名 / bit 位置 / 二进制 / 数值 / 含义 / 标准章节。
   - 顶部搜索框可按中/英文字段名过滤；「显示宏块」可开关宏块级明细。
   - H.265 码流此页会提示暂不支持（待 P5）。

4. **④ 帧预览**（H.264 / HEVC）
   - 帧步进控件（首/上/下/末帧 + 帧号跳转）逐帧浏览；缩放滑块放大观察。
   - 右侧「叠加图层」独立开关：**宏块/子块（HEVC 为 CTU/CU/PU）分类配色**、**QP 热力**、**运动矢量**、**帧内预测方向**、**帧间参考关系**。
   - **HEVC 专属图层**：**TU / RQT 变换块**（红色虚线网格）、**SAO 类型**（按 EO/BO/合并/关闭分类着色）。
   - 图例面板列出全部块类型颜色；信息面板给出帧类型/POC/QP 范围/子块统计。
   - 鼠标悬停任意子块显示其类型/尺寸/预测方向/QP/MV。
   - 首次进入会启动**后台解码**并显示实时进度（大流不再阻塞界面）。

5. **⑤ 原始数据**（H.264）
   - 逐帧（解码序）十六进制视图，按 **起始码 / NAL 头 / 参数集(SPS/PPS) / SEI / 片头 / 各宏块** 分段着色，右侧有图例与分段列表。
   - **点击某宏块的字节块 → 自动切到「④ 帧预览」并高亮该宏块**；反之在帧预览点击宏块 → 原始数据页滚动并高亮其字节块（双向联动）。
   - CAVLC 码流的宏块字节区间精确；CABAC 因算术编码不按 bit 对齐，为近似区间（页面顶部标注）。

---

## 配置说明

- 配置文件：`~/.streamtool/config.json`（首启动自动生成，可手动编辑）。

```json
{
  "ffmpeg": "/usr/bin/ffmpeg",
  "ffprobe": "/usr/bin/ffprobe",
  "jm_ldecod": "/path/to/JM/bin/ldecod.exe",
  "hm_analyser": "/path/to/HM/bin/TAppDecoderAnalyserStatic"
}
```

- 优先级：**界面填写 > `~/.streamtool/config.json` > 环境 `JMHM_ROOT` > 自动探测**。
- 工程数据在 `workdir/<project_id>/`，可整目录拷贝迁移；`workdir/` 已被 `.gitignore` 忽略。

---

## REST 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/config` | 读取配置 + 校验状态 |
| POST | `/api/config` | 保存配置（JSON: `ffmpeg`/`ffprobe`/`jm_ldecod`/`hm_analyser`） |
| POST | `/api/config/verify` | 重新校验各工具 |
| POST | `/api/project/upload` | 上传本机文件建立工程（multipart: `file`），默认方式 |
| POST | `/api/project` | 按服务器本机路径建立工程（JSON: `input_path`），大文件免拷贝 |
| GET  | `/api/projects` | 列出所有工程 |
| GET  | `/api/project/{id}` | 工程详情 |
| GET  | `/api/project/{id}/bitrate` | 码率分析（逐帧/逐秒/GOP + 波动汇总） |
| GET  | `/api/project/{id}/syntax` | 语法总览：帧列表（触发 JM 解码+解析，惰性缓存，仅 H.264） |
| GET  | `/api/project/{id}/frame/{index}/syntax` | 单帧语法树（NAL 字段 + 宏块字段）；`?mbs=false` 省略宏块 |
| GET  | `/api/project/{id}/framemap` | 显示序 ↔ 解码序 ↔ POC 映射 |
| GET  | `/api/project/{id}/frame/{display}/image` | 按显示序取帧 PNG |
| GET  | `/api/project/{id}/frame/{decode}/overlay` | 按解码序取叠加数据（子块配色/QP/MV） |
| GET  | `/api/project/{id}/refgraph` | 帧间参考关系图（节点+边） |
| GET  | `/api/project/{id}/frame/{decode}/rawmap` | 原始数据分段（起始码/Header/宏块 + hex 数据） |
| POST | `/api/project/{id}/decode` | 启动后台解码任务（幂等，已解码则直接返回 done） |
| GET  | `/api/project/{id}/decode/status` | 查询解码进度 `{state,done,total,message,error}` |
| GET  | `/api/health` | 健康检查 |

---

## Docker 部署（可选）

若需一体化、免手动装依赖，可用如下 `Dockerfile`（FFmpeg 内置；JM/HM 如需可在镜像内一并编译或挂载）：

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y ffmpeg python3 python3-pip && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN pip3 install -r requirements.txt
EXPOSE 8731
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8731"]
```

```bash
docker build -t h26x-assistant .
docker run --rm -p 8731:8731 \
  -v /path/to/JM:/opt/JM -v /path/to/HM:/opt/HM \
  -e JMHM_ROOT=/opt h26x-assistant
```

---

## 目录结构

```
h26x-assistant/
├── app/                后端(Python / FastAPI)
│   ├── main.py         入口 + REST 接口 + 静态前端挂载
│   ├── config.py       工具路径探测/校验/持久化
│   ├── project.py      工程管理：探测 codec、解封装、workdir
│   ├── bitrate.py      码率分析：逐帧/逐秒/GOP 平均 + 波动
│   ├── decoder.py      JM 解码驱动：运行 ldecod 生成 trace(H.264)
│   ├── jm_trace_parser.py  解析 trace_dec.txt → 按帧组织的语法结构
│   ├── syntax.py       语法业务层：解码+解析+中英文字典标注+缓存
│   ├── preview.py      帧图像提取 + 显示序↔解码序↔POC 映射
│   ├── mb_partition.py 宏块分割几何还原(H.264 Table 7-11~7-18) → 子块配色
│   ├── overlay.py      每帧叠加数据：分割/QP/MV/帧内方向/帧间参考
│   ├── nal_bytes.py    Annex-B 起始码/NAL 字节区间扫描
│   ├── rawmap.py       每帧原始数据分段(起始码/Header/宏块字节区间)
│   ├── hm_decoder.py   HM 解码驱动(HEVC): 生成 trace/cu_dump/参考列表
│   ├── hm_trace_parser.py  解析 HM TraceDec.txt → VPS/SPS/PPS/Slice 头
│   ├── hm_cu.py        解析 cu_dump.csv → 按解码序切段的逐 CU 数据
│   ├── hevc_syntax.py  HEVC 语法业务层(平行于 syntax.py)
│   ├── hevc_overlay.py HEVC 叠加: CTU/CU/PU 几何 + QP/MV/参考
│   ├── hevc_rawmap.py  HEVC 原始数据 NAL 级分段(2字节 NAL 头)
│   └── data/
│       ├── syntax_dict_h264.json  H.264 语法元素中英文名/含义/章节字典
│       └── syntax_dict_h265.json  H.265 语法元素中英文名/含义/章节字典
├── hm_patch/           HEVC 分析用的 HM 重编译补丁与一键脚本
│   ├── TDecCu_cu_dump.patch     逐 CU dump 源码补丁
│   ├── build_hm_analyser.sh     打补丁 + 加宏 + 重编译
│   └── README.md
├── web/                前端(纯静态，无构建，无第三方依赖)
│   ├── index.html
│   ├── style.css
│   ├── chart.js        自研 Canvas 折线/阶梯图(替代 ECharts，离线可用)
│   ├── preview.js      帧预览 + 分层 Canvas 叠加渲染(H.264/HEVC)
│   ├── rawview.js      原始数据 hex 视图 + 宏块↔预览双向联动
│   └── app.js
├── requirements.txt
├── run.sh
└── README.md
```

---

## 路线图

- **P1（已完成）** 工具设置 + 输入解封装 + 码率曲线(逐帧/逐秒/GOP 波动)。
- **P2（已完成）** H.264 逐帧逐宏块语法解析：解析 JM `trace_dec.txt` → 统一 JSON，每字段附中英文名、bit 位置、二进制、数值、含义与标准章节。前端帧列表 + 可折叠语法树 + 字段搜索。
- **P3（已完成）** 帧预览 + 分层叠加：宏块及各类细分子块（帧内 4×4/8×8/16×16、帧间各分割及子分割、Skip/Direct/PCM）**分类配色**、运动矢量、帧内预测方向、QP 热力、帧间参考关系。宏块分割由 mb_type/sub_mb_type 经 H.264 标准表确定性还原（子块拼接零间隙已验证）。
- **P4（已完成）** 原始数据按起始码/Header/宏块分段显示（hex 视图着色）；点击宏块 ↔ 预览图高亮双向联动。CAVLC 精确、CABAC 近似（已标注）。
- **P5（已完成）** H.265/HEVC 全链路：语法(VPS/SPS/PPS/Slice + CU) · 帧预览(CTU 四叉树/CU/PU 分割配色/MV/QP/参考) · 原始数据 NAL 级分段。基于重编译版 HM 分析器(CU dump + trace，见 `hm_patch/`)。参考关系用 HM 输出的精确 L0/L1 列表。

---

## 常见问题

- **红灯 / 工具不可用**：检查路径是否正确、文件是否有可执行权限（`chmod +x`）。FFmpeg 用 `which ffmpeg` 确认。
- **JM/HM 未自动找到**：设置 `JMHM_ROOT` 或在界面手填绝对路径。P1 只依赖 FFmpeg，可先不配 JM/HM。
- **裸流码率曲线的时间轴**：裸 Annex-B 流通常无 PTS，工具按 `帧号 / 帧率` 推算时间；封装文件用真实 PTS。
- **前端打不开图表**：本工具图表为自研 Canvas，无需联网；若空白请查看浏览器控制台并确认 `/static/*.js` 返回 200。

---

## 许可证与致谢

- 本工具（`app/`、`web/`）源码见仓库许可证。
- JM、HM 为 ITU-T / ISO/IEC 官方参考软件，FFmpeg 为 FFmpeg 项目，均遵循各自许可证，本仓库不分发其代码或二进制。
