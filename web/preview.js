/* 帧预览 + 分层叠加渲染（无外部依赖）。
 * 暴露 window.Preview.{init, load, isLoaded}
 * 底图 <canvas id=pvBase> + 叠加 <canvas id=pvOverlay>，共享同一坐标系与缩放。
 */
(function (global) {
  "use strict";

  // 块类型 → 颜色（分类配色）
  var KIND_COLORS = {
    intra_4x4:  "#ff6b6b",
    intra_8x8:  "#ff9f43",
    intra_16x16:"#feca57",
    intra_pcm:  "#c8d6e5",
    inter_16x16:"#54a0ff",
    inter_16x8: "#2e86de",
    inter_8x16: "#48dbfb",
    inter_8x8:  "#00d2d3",
    inter_8x4:  "#1dd1a1",
    inter_4x8:  "#10ac84",
    inter_4x4:  "#0be881",
    skip:       "#576574",
    direct:     "#a55eea",
    unknown:    "#8395a7",
    // HEVC CU 类型
    intra_cu:   "#ff6b6b",
    inter_cu:   "#54a0ff",
    skip_cu:    "#576574",
  };
  var KIND_LABEL = {
    intra_4x4: "帧内 4×4", intra_8x8: "帧内 8×8", intra_16x16: "帧内 16×16",
    intra_pcm: "I_PCM", inter_16x16: "帧间 16×16", inter_16x8: "帧间 16×8",
    inter_8x16: "帧间 8×16", inter_8x8: "帧间 8×8", inter_8x4: "帧间 8×4",
    inter_4x8: "帧间 4×8", inter_4x4: "帧间 4×4", skip: "Skip",
    direct: "B-Direct", unknown: "未知",
    intra_cu: "帧内 CU", inter_cu: "帧间 CU", skip_cu: "Skip CU",
  };
  var PRED_COLORS = { L0: "#00e5ff", L1: "#ff4081", Bi: "#ffea00", Direct: "#b388ff" };

  var st = {
    pid: null, framemap: null, refgraph: null,
    dispIndex: 0, overlay: null, imgW: 0, imgH: 0, zoom: 1,
    img: new Image(),
    layers: { grid: true, qp: false, mv: false, intra: false, ref: false, tu: false, sao: false },
    overlayCache: {}, hlMb: null, codec: null,
  };

  // SAO 类型 → 颜色/中文
  var SAO_COLORS = {
    off: "#3a4252", eo0: "#4dd0e1", eo90: "#4db6ac",
    eo135: "#7986cb", eo45: "#9575cd", bo: "#ffb74d", merge: "#a1887f",
  };
  var SAO_LABEL = {
    off: "关闭", eo0: "边缘0°", eo90: "边缘90°", eo135: "边缘135°",
    eo45: "边缘45°", bo: "带偏移BO", merge: "合并",
  };

  function $(id) { return document.getElementById(id); }

  async function apiJSON(url) {
    var r = await fetch(url);
    var d = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
    return d;
  }

  function setMsg(t, cls) {
    var e = $("pvMsg"); if (!e) return;
    e.textContent = t || ""; e.className = "msg" + (cls ? " " + cls : "");
  }

  async function load(projectId, codec) {
    st.pid = projectId;
    st.codec = codec;
    st.overlayCache = {};
    // HEVC 专属图层(TU/SAO)显隐
    document.querySelectorAll(".hevc-only").forEach(function (el) {
      el.hidden = (codec !== "hevc");
    });
    if (codec !== "h264" && codec !== "hevc") {
      $("pvNeedProj").hidden = false; $("pvMain").hidden = true;
      $("pvNeedProj").innerHTML = "当前为 " + (codec || "").toUpperCase() +
        " 码流，帧预览暂不支持";
      return;
    }
    $("pvNeedProj").hidden = true; $("pvMain").hidden = false;
    setMsg("正在准备帧映射与图像…");
    try {
      if (global.App && global.App.ensureDecoded) {
        await global.App.ensureDecoded(st.pid, function (s) {
          setMsg(global.App.progressText(s) || "解码中…");
        });
      }
      st.framemap = await apiJSON("/api/project/" + st.pid + "/framemap");
      st.refgraph = await apiJSON("/api/project/" + st.pid + "/refgraph");
      st.dispIndex = 0;
      renderLegend();
      await showFrame(0);
      setMsg("");
    } catch (e) {
      setMsg("加载失败: " + e.message, "err");
    }
  }

  function dispEntry(i) { return st.framemap.display_order[i]; }

  async function showFrame(dispIdx) {
    if (!st.framemap) return;
    var n = st.framemap.num_frames;
    dispIdx = Math.max(0, Math.min(n - 1, dispIdx));
    st.dispIndex = dispIdx;
    var entry = dispEntry(dispIdx);

    $("pvFrameLabel").textContent =
      "显示 " + dispIdx + " / " + (n - 1) + "  ·  " + entry.slice_type +
      "  ·  POC " + entry.poc + "  ·  解码#" + entry.decode_index;

    // 载入底图
    await loadImage("/api/project/" + st.pid + "/frame/" + dispIdx + "/image");
    // 载入叠加(按解码序)
    var di = entry.decode_index;
    if (!st.overlayCache[di]) {
      st.overlayCache[di] = await apiJSON(
        "/api/project/" + st.pid + "/frame/" + di + "/overlay");
    }
    st.overlay = st.overlayCache[di];
    st.imgW = st.overlay.width; st.imgH = st.overlay.height;
    resizeCanvases();
    drawAll();
    renderInfo();
  }

  function loadImage(url) {
    return new Promise(function (resolve) {
      st.img = new Image();
      st.img.onload = function () { resolve(); };
      st.img.onerror = function () { resolve(); };
      st.img.src = url + "?t=" + Date.now();
    });
  }

  function resizeCanvases() {
    var w = Math.round(st.imgW * st.zoom), h = Math.round(st.imgH * st.zoom);
    ["pvBase", "pvOverlay"].forEach(function (id) {
      var c = $(id); c.width = w; c.height = h;
      c.style.width = w + "px"; c.style.height = h + "px";
    });
    var wrap = $("pvCanvasWrap");
    wrap.style.minHeight = Math.min(h + 2, 640) + "px";
  }

  function drawAll() {
    drawBase();
    drawOverlay();
  }

  function drawBase() {
    var c = $("pvBase"), ctx = c.getContext("2d");
    ctx.clearRect(0, 0, c.width, c.height);
    if (st.img && st.img.width) {
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(st.img, 0, 0, c.width, c.height);
    } else {
      ctx.fillStyle = "#111"; ctx.fillRect(0, 0, c.width, c.height);
    }
  }

  function drawOverlay() {
    var c = $("pvOverlay"), ctx = c.getContext("2d");
    ctx.clearRect(0, 0, c.width, c.height);
    if (!st.overlay) return;
    var z = st.zoom;
    if (st.layers.qp) drawQpLayer(ctx, z);
    if (st.layers.sao) drawSaoLayer(ctx, z);
    if (st.layers.grid) drawGridLayer(ctx, z);
    if (st.layers.tu) drawTuLayer(ctx, z);
    if (st.layers.intra) drawIntraLayer(ctx, z);
    if (st.layers.mv) drawMvLayer(ctx, z);
    if (st.layers.ref) drawRefBadge(ctx, z);
    if (st.hlMb != null) drawHighlight(ctx, z);
  }

  function drawTuLayer(ctx, z) {
    if (!st.overlay.tu) return;
    ctx.strokeStyle = "#ff5252cc"; ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    st.overlay.tu.forEach(function (t) {
      ctx.strokeRect(t.x * z + 0.5, t.y * z + 0.5, t.w * z - 1, t.h * z - 1);
    });
    ctx.setLineDash([]);
  }

  function drawSaoLayer(ctx, z) {
    if (!st.overlay.sao) return;
    st.overlay.sao.forEach(function (s) {
      ctx.fillStyle = hexA(SAO_COLORS[s.kind] || "#3a4252", 0.45);
      ctx.fillRect(s.x * z, s.y * z, s.size * z, s.size * z);
      ctx.strokeStyle = "#00000055"; ctx.lineWidth = 1;
      ctx.strokeRect(s.x * z + 0.5, s.y * z + 0.5, s.size * z, s.size * z);
    });
  }

  function drawHighlight(ctx, z) {
    var b = null;
    for (var i = 0; i < st.overlay.blocks.length; i++) {
      if (st.overlay.blocks[i].mb_index === st.hlMb) { b = st.overlay.blocks[i]; break; }
    }
    if (!b) return;
    ctx.save();
    ctx.strokeStyle = "#ffd54f"; ctx.lineWidth = 3;
    ctx.strokeRect(b.x * z + 1.5, b.y * z + 1.5, b.w * z - 3, b.h * z - 3);
    ctx.fillStyle = "rgba(255,213,79,0.25)";
    ctx.fillRect(b.x * z, b.y * z, b.w * z, b.h * z);
    ctx.restore();
  }

  function drawGridLayer(ctx, z) {
    st.overlay.blocks.forEach(function (b) {
      // 宏块外框
      ctx.strokeStyle = "#ffffff55"; ctx.lineWidth = 1;
      ctx.strokeRect(b.x * z + 0.5, b.y * z + 0.5, b.w * z, b.h * z);
      // 子块填充 + 边框（分类配色）
      b.partitions.forEach(function (p) {
        var col = KIND_COLORS[p.kind] || "#8395a7";
        ctx.fillStyle = hexA(col, 0.28);
        ctx.fillRect(p.x * z, p.y * z, p.w * z, p.h * z);
        ctx.strokeStyle = hexA(col, 0.95); ctx.lineWidth = 1;
        ctx.strokeRect(p.x * z + 0.5, p.y * z + 0.5, p.w * z - 1, p.h * z - 1);
      });
    });
  }

  function qpColor(qp) {
    // 低QP(细) 蓝 → 高QP(粗) 红
    var t = Math.max(0, Math.min(1, (qp - 10) / 40));
    var r = Math.round(40 + t * 200), g = Math.round(120 - t * 90), b = Math.round(220 - t * 180);
    return "rgba(" + r + "," + g + "," + b + ",0.5)";
  }
  function drawQpLayer(ctx, z) {
    st.overlay.blocks.forEach(function (b) {
      ctx.fillStyle = qpColor(b.qp);
      ctx.fillRect(b.x * z, b.y * z, b.w * z, b.h * z);
      if (z >= 1.4) {
        ctx.fillStyle = "#fff"; ctx.font = Math.round(7 * z / 1.2) + "px monospace";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(b.qp, (b.x + b.w / 2) * z, (b.y + b.h / 2) * z);
      }
    });
  }

  var I4_DIR = { 0: [0, 1], 1: [1, 0], 3: [1, 1], 4: [-1, 1], 5: [0.5, 1],
                 6: [1, 0.5], 7: [-0.5, 1], 8: [1, -0.5] }; // 近似方向向量
  function drawIntraLayer(ctx, z) {
    ctx.strokeStyle = "#ffe08a"; ctx.lineWidth = 1;
    st.overlay.blocks.forEach(function (b) {
      b.partitions.forEach(function (p) {
        if (p.intra_mode == null || p.pred !== "Intra") return;
        var cx = (p.x + p.w / 2) * z, cy = (p.y + p.h / 2) * z;
        var d = I4_DIR[p.intra_mode];
        var len = Math.min(p.w, p.h) * z * 0.4;
        if (p.intra_mode === 2 || !d) {  // DC：画点
          ctx.fillStyle = "#ffe08a";
          ctx.beginPath(); ctx.arc(cx, cy, 1.6, 0, 6.28); ctx.fill();
          return;
        }
        var nx = d[0], ny = d[1], nn = Math.hypot(nx, ny) || 1;
        ctx.beginPath();
        ctx.moveTo(cx - nx / nn * len, cy - ny / nn * len);
        ctx.lineTo(cx + nx / nn * len, cy + ny / nn * len);
        ctx.stroke();
      });
    });
  }

  function drawMvLayer(ctx, z) {
    st.overlay.blocks.forEach(function (b) {
      b.partitions.forEach(function (p) {
        if (!p.mvd) return;
        var cx = (p.x + p.w / 2) * z, cy = (p.y + p.h / 2) * z;
        // mvd 单位 1/4 像素；放大显示
        var scale = z * 0.25 * 2.0;
        var ex = cx + p.mvd[0] * scale, ey = cy + p.mvd[1] * scale;
        var col = PRED_COLORS[p.pred] || "#00e5ff";
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey); ctx.stroke();
        // 箭头
        var a = Math.atan2(ey - cy, ex - cx), hl = 3;
        ctx.beginPath();
        ctx.moveTo(ex, ey);
        ctx.lineTo(ex - hl * Math.cos(a - 0.4), ey - hl * Math.sin(a - 0.4));
        ctx.lineTo(ex - hl * Math.cos(a + 0.4), ey - hl * Math.sin(a + 0.4));
        ctx.closePath(); ctx.fill();
        ctx.fillStyle = col;
        ctx.beginPath(); ctx.arc(cx, cy, 1, 0, 6.28); ctx.fill();
      });
    });
  }

  function drawRefBadge(ctx, z) {
    // 在预览上以文字提示参考帧（详细图在右侧信息面板）
    var di = dispEntry(st.dispIndex).decode_index;
    var edges = st.refgraph.edges.filter(function (e) { return e.from === di; });
    if (!edges.length) return;
    var nodes = {};
    st.refgraph.nodes.forEach(function (n) { nodes[n.decode_index] = n; });
    var txt = "参考→ " + edges.map(function (e) {
      return e.list + ":POC" + (nodes[e.to] ? nodes[e.to].poc : "?");
    }).join("  ");
    ctx.fillStyle = "rgba(0,0,0,0.6)"; ctx.fillRect(4, 4, ctx.measureText(txt).width + 60, 18);
    ctx.fillStyle = "#8be9fd"; ctx.font = "12px monospace";
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillText(txt, 8, 7);
  }

  function legendItem(body, color, label) {
    var item = document.createElement("div"); item.className = "legend-item";
    var sw = document.createElement("span"); sw.className = "legend-swatch";
    sw.style.background = color;
    item.appendChild(sw);
    item.appendChild(document.createTextNode(label));
    body.appendChild(item);
  }
  function legendSep(body) {
    var hr = document.createElement("div");
    hr.style.cssText = "height:1px;background:#263049;margin:6px 0";
    body.appendChild(hr);
  }

  function renderLegend() {
    var body = $("pvLegendBody"); body.innerHTML = "";
    var isHevc = (st.codec === "hevc");
    // 块类型：按 codec 过滤(HEVC 只列 CU 类；H.264 列宏块子块类)
    var blockKeys = Object.keys(KIND_LABEL).filter(function (k) {
      var isCU = (k.indexOf("_cu") >= 0);
      return isHevc ? isCU : !isCU;
    });
    blockKeys.forEach(function (k) { legendItem(body, KIND_COLORS[k], KIND_LABEL[k]); });
    legendSep(body);
    Object.keys(PRED_COLORS).forEach(function (k) {
      legendItem(body, PRED_COLORS[k], "MV " + k);
    });
    if (isHevc) {
      legendSep(body);
      Object.keys(SAO_LABEL).forEach(function (k) {
        legendItem(body, SAO_COLORS[k], "SAO " + SAO_LABEL[k]);
      });
    }
  }

  function renderInfo() {
    var o = st.overlay, e = dispEntry(st.dispIndex);
    var counts = {};
    o.blocks.forEach(function (b) {
      b.partitions.forEach(function (p) { counts[p.kind] = (counts[p.kind] || 0) + 1; });
    });
    var rows = [
      ["帧类型", o.slice_type + " 片"],
      ["POC", o.poc], ["解码序", e.decode_index], ["显示序", st.dispIndex],
      ["分辨率", o.width + "×" + o.height],
      ["宏块", o.mb_width + "×" + o.mb_height + " = " + o.num_blocks],
      ["Slice QP", o.slice_qp], ["QP 范围", o.qp_range.join(" ~ ")],
    ];
    var html = rows.map(function (r) {
      return "<div><span class='k'>" + r[0] + "</span>" + r[1] + "</div>";
    }).join("");
    html += "<div style='margin-top:6px;color:#7d89a5'>子块统计：</div>";
    Object.keys(counts).sort().forEach(function (k) {
      html += "<div><span class='k' style='color:" + (KIND_COLORS[k] || "#888") + "'>" +
        (KIND_LABEL[k] || k) + "</span>" + counts[k] + "</div>";
    });
    $("pvInfoBody").innerHTML = html;
  }

  function hexA(hex, a) {
    var h = hex.replace("#", "");
    var r = parseInt(h.substr(0, 2), 16), g = parseInt(h.substr(2, 2), 16), b = parseInt(h.substr(4, 2), 16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  }

  // hover tooltip：命中子块
  function bindHover() {
    var ov = $("pvOverlay"), wrap = $("pvCanvasWrap"), tip = $("pvTip");
    wrap.addEventListener("mousemove", function (ev) {
      if (!st.overlay) return;
      var rect = ov.getBoundingClientRect();
      var px = (ev.clientX - rect.left) / st.zoom, py = (ev.clientY - rect.top) / st.zoom;
      var hit = null;
      for (var i = 0; i < st.overlay.blocks.length && !hit; i++) {
        var b = st.overlay.blocks[i];
        if (px >= b.x && px < b.x + b.w && py >= b.y && py < b.y + b.h) {
          for (var j = 0; j < b.partitions.length; j++) {
            var p = b.partitions[j];
            if (px >= p.x && px < p.x + p.w && py >= p.y && py < p.y + p.h) {
              hit = { b: b, p: p }; break;
            }
          }
          if (!hit) hit = { b: b, p: b.partitions[0] };
        }
      }
      if (hit) {
        tip.style.display = "block";
        tip.style.left = (ev.clientX - rect.left + 12) + "px";
        tip.style.top = (ev.clientY - rect.top + 8) + "px";
        var p = hit.p;
        tip.innerHTML = "MB " + hit.b.mb_index + " · QP " + hit.b.qp +
          "<br>" + (KIND_LABEL[p.kind] || p.kind) + " (" + p.w + "×" + p.h + ")" +
          "<br>pred " + p.pred +
          (p.intra_mode != null ? " · 模式 " + p.intra_mode : "") +
          (p.mvd ? "<br>MV(" + p.mvd[0] + "," + p.mvd[1] + ") ref" + (p.ref_idx || 0) : "");
      } else { tip.style.display = "none"; }
    });
    wrap.addEventListener("mouseleave", function () { tip.style.display = "none"; });
    // 点击宏块 → 高亮 + 通知原始数据页联动
    wrap.addEventListener("click", function (ev) {
      if (!st.overlay) return;
      var rect = ov.getBoundingClientRect();
      var px = (ev.clientX - rect.left) / st.zoom, py = (ev.clientY - rect.top) / st.zoom;
      for (var i = 0; i < st.overlay.blocks.length; i++) {
        var b = st.overlay.blocks[i];
        if (px >= b.x && px < b.x + b.w && py >= b.y && py < b.y + b.h) {
          st.hlMb = b.mb_index; drawOverlay();
          if (global.RawView && global.RawView.showMbFromPreview)
            global.RawView.showMbFromPreview(st.overlay.decode_index, b.mb_index);
          break;
        }
      }
    });
  }

  // 供原始数据页反向调用：切到该解码帧并高亮宏块
  function highlightMb(decodeIndex, mb) {
    var di = st.framemap ? st.framemap.decode_to_display[decodeIndex] : null;
    if (di == null) { st.hlMb = mb; if (st.overlay) drawOverlay(); return; }
    if (di !== st.dispIndex) {
      showFrame(di).then(function () { st.hlMb = mb; drawOverlay(); });
    } else {
      st.hlMb = mb; drawOverlay();
    }
  }

  function init() {
    $("pvFirst").addEventListener("click", function () { showFrame(0); });
    $("pvPrev").addEventListener("click", function () { showFrame(st.dispIndex - 1); });
    $("pvNext").addEventListener("click", function () { showFrame(st.dispIndex + 1); });
    $("pvLast").addEventListener("click", function () { showFrame(st.framemap.num_frames - 1); });
    $("pvJump").addEventListener("change", function () {
      var v = parseInt(this.value, 10); if (!isNaN(v)) showFrame(v);
    });
    $("pvZoom").addEventListener("input", function () {
      st.zoom = parseFloat(this.value);
      $("pvZoomVal").textContent = Math.round(st.zoom * 100) + "%";
      resizeCanvases(); drawAll();
    });
    var map = { lyGrid: "grid", lyQp: "qp", lyMv: "mv", lyIntra: "intra",
                lyRef: "ref", lyTu: "tu", lySao: "sao" };
    Object.keys(map).forEach(function (id) {
      $(id).addEventListener("change", function () {
        st.layers[map[id]] = this.checked; drawOverlay();
      });
    });
    bindHover();
  }

  global.Preview = {
    init: init, load: load,
    isLoaded: function () { return !!st.framemap; },
    highlightMb: highlightMb,
  };
})(window);
