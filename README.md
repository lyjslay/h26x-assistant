# h26x-assistant · H.264/H.265 码流分析工具

基于 **JM**(H.264/AVC 官方参考解码器)、**HM**(H.265/HEVC 官方参考解码器/分析器) 与 **FFmpeg**
的图形化码流分析工具。后端 Python(FastAPI)，前端纯 Web(原生 Canvas，**零第三方 JS 依赖，完全离线可用**)。

在浏览器里查看码率曲线、逐帧逐宏块语法、预测块划分/运动矢量/参考关系叠加、原始字节分段——
把参考解码器的深度信息可视化出来，用于码流调试、教学与算法分析。

> **当前进度：P1 已完成** —— ① 工具路径设置 + 输入(含自动解封装) + ② 码率分析(逐帧/逐秒/GOP 平均与波动)。
> 路线图见文末，后续 P2–P5 陆续实现语法解析、帧预览叠加、原始数据联动、HEVC CU 级分析。

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
| 3 | 码流语法解析：每字段中英文名 + 数值含义，按帧排列 | ⏳ P2 |
| 4 | 逐帧预览 + 叠加绘制：宏块及各类细分子块配色、MV、参考方向、QP、帧内/帧间参考关系 | ⏳ P3 |
| 5 | 原始数据按起始码/Header/宏块分段；点击宏块在预览图高亮(双向联动) | ⏳ P4 |
| — | H.265/HEVC CU 级深度解析 | ⏳ P5 |

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

### 3) HM —— H.265 参考解码器/分析器（P5 需要）

```bash
# 官方仓库：https://vcgit.hhi.fraunhofer.de/jvet/HM
cd HM/build/linux && make      # 生成 bin/TAppDecoderAnalyserStatic 等
./bin/TAppDecoderAnalyserStatic # 验证
```

> HEVC **CU 级完整语法**需要在编译时开启 `ENC_DEC_TRACE=1`
> （编辑 `HM/build/linux/lib/TLibDecoderAnalyser/makefile` 的 `DEFS` 追加 `-DENC_DEC_TRACE=1` 后重新 `make`）。
> 该步骤仅 P5 用到，P1–P4 不需要。

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

3. **③④⑤** 标签为 P2–P4 功能占位，实现后自动可用。

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
│   └── bitrate.py      码率分析：逐帧/逐秒/GOP 平均 + 波动
├── web/                前端(纯静态，无构建，无第三方依赖)
│   ├── index.html
│   ├── style.css
│   ├── chart.js        自研 Canvas 折线/阶梯图(替代 ECharts，离线可用)
│   └── app.js
├── requirements.txt
├── run.sh
└── README.md
```

---

## 路线图

- **P1（已完成）** 工具设置 + 输入解封装 + 码率曲线(逐帧/逐秒/GOP 波动)。
- **P2** H.264 逐帧逐宏块语法解析：解析 JM `trace_dec.txt` → 统一 JSON，每字段附中英文名与含义。
- **P3** 帧预览 + 分层叠加：宏块及各类细分子块（帧内 4×4/8×8/16×16、帧间各分割及子分割、Skip/PCM 等）**分类配色**、运动矢量、参考方向、QP 热力、帧内/帧间参考关系。
- **P4** 原始数据按起始码/Header/宏块分段显示；点击宏块 ↔ 预览图高亮双向联动。
- **P5** H.265/HEVC CU 级（CTU 四叉树、PU 分割、TU/RQT），接入同一前端。

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
