/* 前端应用逻辑：设置/输入 + 码率分析。无外部依赖。 */
(function () {
  "use strict";

  const state = { config: {}, verify: {}, project: null, bitrate: null };

  const CFG_LABELS = {
    ffmpeg: "ffmpeg",
    ffprobe: "ffprobe",
    jm_ldecod: "JM 解码器",
    hm_analyser: "HM 分析器",
  };

  function $(sel) { return document.querySelector(sel); }
  function el(tag, cls, txt) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }
  async function api(path, opts) {
    const r = await fetch(path, opts);
    const data = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(data.detail || ("HTTP " + r.status));
    return data;
  }

  /* ---------- Tabs ---------- */
  function initTabs() {
    document.querySelectorAll(".tab").forEach(function (t) {
      t.addEventListener("click", function () {
        if (t.classList.contains("disabled")) return;
        document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
        document.querySelectorAll(".panel").forEach(x => x.classList.remove("active"));
        t.classList.add("active");
        $("#tab-" + t.dataset.tab).classList.add("active");
        if (t.dataset.tab === "bitrate" && state.project && !state.bitrate) loadBitrate();
      });
    });
  }

  /* ---------- 配置 ---------- */
  function renderConfig() {
    const grid = $("#cfgGrid");
    grid.innerHTML = "";
    Object.keys(CFG_LABELS).forEach(function (key) {
      grid.appendChild(el("div", "cfg-label", CFG_LABELS[key]));
      const input = el("input");
      input.type = "text"; input.id = "cfg_" + key;
      input.value = state.config[key] || "";
      input.placeholder = "自动探测";
      grid.appendChild(input);
      const v = state.verify[key] || {};
      const led = el("div", "led " + (v.ok ? "ok" : "fail"));
      led.appendChild(el("span", "dot"));
      led.appendChild(el("span", null, v.ok ? "可用" : "不可用"));
      led.title = v.version || v.error || "";
      grid.appendChild(led);
    });
  }

  async function loadConfig() {
    const d = await api("/api/config");
    state.config = d.config; state.verify = d.verify;
    renderConfig();
  }

  async function saveConfig() {
    const body = {};
    Object.keys(CFG_LABELS).forEach(k => { body[k] = $("#cfg_" + k).value.trim(); });
    setMsg("#cfgMsg", "保存中…", "");
    try {
      const d = await api("/api/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      state.config = d.config; state.verify = d.verify;
      renderConfig();
      const allok = Object.values(d.verify).every(x => x.ok);
      setMsg("#cfgMsg", allok ? "全部工具可用 ✓" : "已保存，部分工具不可用", allok ? "ok" : "err");
    } catch (e) { setMsg("#cfgMsg", "保存失败: " + e.message, "err"); }
  }

  async function verifyConfig() {
    setMsg("#cfgMsg", "校验中…", "");
    try {
      const d = await api("/api/config/verify", { method: "POST" });
      state.verify = d.verify; renderConfig();
      const allok = Object.values(d.verify).every(x => x.ok);
      setMsg("#cfgMsg", allok ? "全部工具可用 ✓" : "部分工具不可用", allok ? "ok" : "err");
    } catch (e) { setMsg("#cfgMsg", "校验失败: " + e.message, "err"); }
  }

  function setMsg(sel, txt, cls) {
    const e = $(sel); e.textContent = txt;
    e.className = "msg" + (cls ? " " + cls : "");
  }

  /* ---------- 工程 ---------- */
  function onProjectCreated(proj) {
    state.project = proj; state.bitrate = null;
    renderProjInfo();
    setMsg("#setupMsg", "工程已建立，切到「② 码率分析」查看曲线", "ok");
    enableTab("bitrate");
  }

  // 浏览器上传本机文件(默认方式)，带进度条
  function uploadFile(file) {
    setMsg("#setupMsg", "", "");
    const fd = new FormData();
    fd.append("file", file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/project/upload");
    const box = $("#uploadProgress"), bar = $("#uploadBar"), pct = $("#uploadPct");
    box.hidden = false; bar.style.width = "0%"; pct.textContent = "";

    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) {
        const p = Math.round(e.loaded / e.total * 100);
        bar.style.width = p + "%";
        pct.textContent = p + "%  (" + (e.loaded / 1048576).toFixed(1) + "/" +
          (e.total / 1048576).toFixed(1) + " MB)";
      }
    };
    xhr.upload.onload = function () {
      bar.style.width = "100%"; pct.textContent = "上传完成，解析中…（封装文件将自动解封装）";
    };
    xhr.onload = function () {
      box.hidden = true;
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status >= 200 && xhr.status < 300 && data.project) {
        onProjectCreated(data.project);
      } else {
        setMsg("#setupMsg", "失败: " + (data.detail || ("HTTP " + xhr.status)), "err");
      }
    };
    xhr.onerror = function () { box.hidden = true; setMsg("#setupMsg", "上传失败：网络错误", "err"); };
    xhr.send(fd);
  }

  // 高级：按服务器本机路径建立工程
  async function createProjectByPath() {
    const path = $("#inputPath").value.trim();
    if (!path) { setMsg("#setupMsg", "请填写服务器本机路径", "err"); return; }
    setMsg("#setupMsg", "解析中…（封装文件将自动解封装，稍候）", "");
    try {
      const d = await api("/api/project", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input_path: path }),
      });
      onProjectCreated(d.project);
    } catch (e) { setMsg("#setupMsg", "失败: " + e.message, "err"); }
  }

  function renderProjInfo() {
    const p = state.project;
    const box = $("#projInfo");
    if (!p) { box.classList.remove("show"); return; }
    const rows = [
      ["工程 ID", p.id],
      ["输入文件", p.input_name],
      ["来源", p.source_kind === "demuxed" ? "封装文件(已解封装)" : "裸码流"],
      ["编码", p.codec.toUpperCase()],
      ["分辨率", p.width + " × " + p.height],
      ["帧率", p.fps + " fps"],
      ["时长", p.duration + " s"],
      ["Profile/Level", (p.profile || "-") + " / " + (p.level || "-")],
      ["裸流大小", (p.elementary_bytes / 1024 / 1024).toFixed(2) + " MB"],
    ];
    const tbl = el("table");
    rows.forEach(function (r) {
      const tr = el("tr");
      tr.appendChild(el("td", "k", r[0]));
      tr.appendChild(el("td", "v", String(r[1])));
      tbl.appendChild(tr);
    });
    box.innerHTML = ""; box.appendChild(tbl); box.classList.add("show");
  }

  function enableTab(name) {
    const t = document.querySelector('.tab[data-tab="' + name + '"]');
    if (t) t.classList.remove("disabled");
  }

  /* ---------- 码率 ---------- */
  let chart = null;

  async function loadBitrate() {
    if (!state.project) { setMsg("#brMsg", "请先在「① 设置与输入」加载文件", "err"); return; }
    setMsg("#brMsg", "分析中…", "");
    try {
      state.bitrate = await api("/api/project/" + state.project.id + "/bitrate");
      renderSummary();
      drawBitrate();
      setMsg("#brMsg", "", "");
    } catch (e) { setMsg("#brMsg", "失败: " + e.message, "err"); }
  }

  function renderSummary() {
    const s = state.bitrate.summary;
    const cards = [
      ["总码率", ChartFmt.fmtBps(s.overall_bitrate_bps)],
      ["帧数", s.num_frames],
      ["GOP 数", s.num_gops],
      ["时长", s.duration + " s"],
      ["峰值帧", ChartFmt.fmtBits(s.frame_bits_max)],
      ["GOP 均值", ChartFmt.fmtBps(s.gop_avg_mean)],
      ["GOP 波动σ", ChartFmt.fmtBps(s.gop_avg_std)],
      ["GOP 峰均比", s.gop_peak_to_mean + "×"],
    ];
    const box = $("#brSummary"); box.innerHTML = "";
    cards.forEach(function (c) {
      const card = el("div", "card");
      card.appendChild(el("div", "cv", String(c[1])));
      card.appendChild(el("div", "cl", c[0]));
      box.appendChild(card);
    });
  }

  function drawBitrate() {
    const br = state.bitrate;
    const mode = document.querySelector('input[name="brmode"]:checked').value;
    const showGop = $("#showGop").checked;
    const showTypes = $("#showTypes").checked;
    const series = [], markers = [];

    if (mode === "frame") {
      series.push({
        name: "逐帧码率", color: "#3b82f6", type: "line", width: 1.2,
        fill: "#3b82f622",
        points: br.per_frame.map(f => ({ x: f.idx, y: f.bits, meta: f })),
      });
      if (showTypes) {
        br.per_frame.forEach(function (f) {
          if (f.key) markers.push({ x: f.idx, y: f.bits, color: "#f59e0b", meta: f });
        });
      }
    } else {
      series.push({
        name: "逐秒码率", color: "#22c55e", type: "line", width: 1.6,
        fill: "#22c55e22",
        points: br.per_second.map(s => ({ x: s.second, y: s.bitrate_bps, meta: s })),
      });
    }

    if (showGop && mode === "frame") {
      // GOP 平均阶梯线：每个 GOP 在其帧范围内画一条水平线
      const pts = [];
      br.per_gop.forEach(function (g) {
        pts.push({ x: g.start_frame, y: g.avg_bitrate_bps, x0: g.start_frame, meta: g });
        pts.push({ x: g.end_frame + 0.99, y: g.avg_bitrate_bps, meta: g });
      });
      series.push({
        name: "GOP 平均", color: "#f472b6", type: "line", width: 2,
        dash: [6, 3], noHover: true, points: pts,
      });
    }

    const opts = {
      xLabel: mode === "frame" ? "帧号" : "时间 (秒)",
      yFmt: ChartFmt.fmtBps,
      tooltip: $("#brTooltip"),
      tipFmt: function (p, s) {
        const m = p.meta || {};
        if (mode === "frame" && m.idx != null) {
          return "帧 " + m.idx + (m.key ? " <b style='color:#f59e0b'>[关键帧]</b>" : "") +
            "<br>大小 " + ChartFmt.fmtBits(m.bits) +
            "<br>码率点 " + ChartFmt.fmtBps(m.bits) +
            "<br>pts " + (m.pts != null ? m.pts.toFixed(3) + "s" : "-");
        }
        if (m.second != null) {
          return "第 " + m.second + " 秒<br>" + ChartFmt.fmtBps(m.bitrate_bps);
        }
        return ChartFmt.fmtBps(p.y);
      },
    };
    if (!chart) chart = new LineChart($("#brChart"), opts);
    else { chart.opts = Object.assign(chart.opts, opts); }
    chart.setData(series, markers);
  }

  /* ---------- init ---------- */
  function init() {
    initTabs();
    loadConfig();
    $("#btnSaveCfg").addEventListener("click", saveConfig);
    $("#btnVerify").addEventListener("click", verifyConfig);

    // 选择本机文件(默认，上传)
    $("#btnPickFile").addEventListener("click", function () { $("#fileInput").click(); });
    $("#fileInput").addEventListener("change", function () {
      const f = this.files && this.files[0];
      if (!f) return;
      $("#pickedName").textContent = f.name + "  (" + (f.size / 1048576).toFixed(1) + " MB)";
      uploadFile(f);
      this.value = "";  // 允许再次选同一文件
    });

    // 高级：服务器路径
    $("#toggleAdvanced").addEventListener("click", function (e) {
      e.preventDefault();
      const box = $("#advancedBox");
      box.hidden = !box.hidden;
      this.textContent = (box.hidden ? "▸" : "▾") +
        " 高级：使用服务器本机路径(大文件免上传)";
    });
    $("#btnCreateProj").addEventListener("click", createProjectByPath);
    $("#btnReloadBr").addEventListener("click", function () { state.bitrate = null; loadBitrate(); });
    document.querySelectorAll('input[name="brmode"]').forEach(r =>
      r.addEventListener("change", drawBitrate));
    $("#showGop").addEventListener("change", drawBitrate);
    $("#showTypes").addEventListener("change", drawBitrate);
    window.addEventListener("resize", function () { if (chart && state.bitrate) chart.draw(); });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
