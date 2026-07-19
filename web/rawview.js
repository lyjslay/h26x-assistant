/* 原始数据页：逐帧 hex 视图按分段着色 + 点击宏块↔帧预览双向高亮。
 * 暴露 window.RawView.{init, load, isLoaded, showMbFromPreview}
 * 帧按解码序(与 overlay 一致)。
 */
(function (global) {
  "use strict";

  var st = {
    pid: null, codec: null, decodeIndex: 0, numFrames: 0,
    rawmap: null, byteKind: null, byteMb: null, selMb: null,
  };

  function $(id) { return document.getElementById(id); }

  async function apiJSON(url) {
    var r = await fetch(url);
    var d = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
    return d;
  }
  function setMsg(t, cls) {
    var e = $("rwMsg"); if (!e) return;
    e.textContent = t || ""; e.className = "msg" + (cls ? " " + cls : "");
  }

  async function load(projectId, codec) {
    st.pid = projectId; st.codec = codec;
    if (codec !== "h264") {
      $("rwNeedProj").hidden = false; $("rwMain").hidden = true;
      $("rwNeedProj").innerHTML = "当前为 " + (codec || "").toUpperCase() +
        " 码流，原始数据分段暂仅支持 H.264（H.265 待 P5）";
      return;
    }
    $("rwNeedProj").hidden = true; $("rwMain").hidden = false;
    setMsg("正在准备解码与语法解析…");
    try {
      // 借用 framemap 得到帧总数(解码序)
      var fm = await apiJSON("/api/project/" + st.pid + "/framemap");
      st.numFrames = fm.num_frames;
      st.decodeIndex = 0;
      await showFrame(0);
      setMsg("");
    } catch (e) { setMsg("加载失败: " + e.message, "err"); }
  }

  async function showFrame(di) {
    di = Math.max(0, Math.min(st.numFrames - 1, di));
    st.decodeIndex = di; st.selMb = null;
    setMsg("");
    try {
      st.rawmap = await apiJSON(
        "/api/project/" + st.pid + "/frame/" + di + "/rawmap?hexdata=true");
    } catch (e) { setMsg("加载失败: " + e.message, "err"); return; }
    render();
  }

  function render() {
    var d = st.rawmap;
    $("rwFrameLabel").textContent =
      "解码帧 " + d.decode_index + " / " + (st.numFrames - 1) +
      "  ·  " + d.slice_type + "  ·  POC " + d.poc;
    $("rwMapNote").textContent = d.entropy.toUpperCase() +
      (d.mb_mapping === "approx"
        ? " · 宏块字节为近似(CABAC 算术编码不按 bit 对齐)"
        : " · 宏块字节精确(CAVLC)");
    buildByteIndex();
    renderHex();
    renderSegList();
    $("rwSelInfo").innerHTML = "在左侧点击某宏块字节块，将在此显示，并可在「④ 帧预览」高亮。";
    $("rwGotoPreview").hidden = true;
  }

  // 为 hex_base..(hex_base+hex_len) 每个字节标注 kind 与 mb_index
  function buildByteIndex() {
    var d = st.rawmap;
    var base = d.hex_base, len = d.hex_len;
    st.byteKind = new Array(len).fill(null);
    st.byteMb = new Array(len).fill(null);
    var mbSeq = 0;
    d.segments.forEach(function (s) {
      var kind = s.kind;
      if (kind === "mb") { s._mbparity = (mbSeq++ % 2); }
      for (var b = s.byte_start; b < s.byte_end; b++) {
        var i = b - base;
        if (i < 0 || i >= len) continue;
        st.byteKind[i] = s;
      }
    });
  }

  function kindClass(seg) {
    if (!seg) return "";
    switch (seg.kind) {
      case "start_code": return "sc";
      case "nal_header": return "nal_header";
      case "param_set": return "param_set";
      case "sei": return "sei";
      case "slice_header": return "slice_header";
      case "mb": return seg._mbparity ? "mb1" : "mb0";
      default: return "";
    }
  }

  function renderHex() {
    var d = st.rawmap;
    var hex = d.hex, base = d.hex_base, len = d.hex_len;
    var PER = 16;
    var html = [];
    for (var row = 0; row < len; row += PER) {
      var off = (base + row).toString(16).padStart(8, "0");
      var cells = [];
      for (var c = 0; c < PER && row + c < len; c++) {
        var i = row + c;
        var seg = st.byteKind[i];
        var cls = kindClass(seg);
        var mbAttr = "";
        if (seg && seg.kind === "mb") mbAttr = " data-mb='" + seg.mb_index + "'";
        var byteHex = hex.substr(i * 2, 2);
        cells.push("<span class='hex-b " + cls + "'" + mbAttr +
          " data-i='" + i + "'>" + byteHex + "</span>");
      }
      html.push("<div class='hex-row'><span class='hex-off'>" + off +
        "</span><span class='hex-bytes'>" + cells.join("") + "</span></div>");
    }
    var box = $("rwHex");
    box.innerHTML = html.join("");
    if (d.hex_truncated) {
      var w = document.createElement("div");
      w.style.cssText = "color:#d0a24a;padding:6px 0";
      w.textContent = "（数据较大，仅显示前 " + len + " 字节）";
      box.appendChild(w);
    }
    bindHexEvents();
  }

  function bindHexEvents() {
    var box = $("rwHex");
    box.querySelectorAll(".hex-b[data-mb]").forEach(function (el) {
      el.addEventListener("click", function () {
        selectMb(parseInt(this.dataset.mb, 10), true);
      });
      el.addEventListener("mouseenter", function () {
        highlightMb(parseInt(this.dataset.mb, 10), "mbhover", true);
      });
      el.addEventListener("mouseleave", function () {
        clearClass("mbhover");
      });
    });
  }

  function clearClass(cls) {
    $("rwHex").querySelectorAll("." + cls).forEach(function (e) { e.classList.remove(cls); });
  }

  function highlightMb(mb, cls, on) {
    $("rwHex").querySelectorAll(".hex-b[data-mb='" + mb + "']").forEach(function (e) {
      if (on) e.classList.add(cls); else e.classList.remove(cls);
    });
  }

  function selectMb(mb, notifyPreview) {
    clearClass("mbsel");
    highlightMb(mb, "mbsel", true);
    st.selMb = mb;
    var seg = st.rawmap.segments.find(function (s) { return s.kind === "mb" && s.mb_index === mb; });
    var info = "宏块 " + mb;
    if (seg) {
      info += "<br><span class='k'>字节</span>" + seg.byte_start + " ~ " + seg.byte_end +
        " (" + (seg.byte_end - seg.byte_start) + " B)" +
        (seg.approx ? "<br><span style='color:#d0a24a'>CABAC 近似区间</span>" : "");
    }
    $("rwSelInfo").innerHTML = info;
    $("rwGotoPreview").hidden = false;
    // 滚动到该宏块首字节
    var first = $("rwHex").querySelector(".hex-b[data-mb='" + mb + "']");
    if (first) first.scrollIntoView({ block: "nearest", behavior: "smooth" });
    // 通知帧预览联动高亮
    if (notifyPreview && global.Preview && global.Preview.highlightMb) {
      global.Preview.highlightMb(st.decodeIndex, mb);
    }
  }

  // 供帧预览反向调用：预览点宏块 → 这里滚动高亮
  function showMbFromPreview(decodeIndex, mb) {
    if (decodeIndex !== st.decodeIndex) {
      showFrame(decodeIndex).then(function () { selectMb(mb, false); });
    } else {
      selectMb(mb, false);
    }
  }

  function renderSegList() {
    var d = st.rawmap;
    var html = d.segments.filter(function (s) { return s.kind !== "mb"; })
      .map(function (s) {
        return "<div class='seg' data-bs='" + s.byte_start + "'>" +
          "<span>" + esc(s.label) + "</span>" +
          "<span class='rng'>" + s.byte_start + "~" + s.byte_end + "</span></div>";
      }).join("");
    var mbCount = d.segments.filter(function (s) { return s.kind === "mb"; }).length;
    html += "<div class='seg' style='color:#7d89a5'>… 宏块 × " + mbCount + " 个</div>";
    var box = $("rwSegList"); box.innerHTML = html;
    box.querySelectorAll(".seg[data-bs]").forEach(function (el) {
      el.addEventListener("click", function () {
        var bs = parseInt(this.dataset.bs, 10);
        var target = $("rwHex").querySelector(".hex-b[data-i='" + (bs - d.hex_base) + "']");
        if (target) target.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function init() {
    $("rwFirst").addEventListener("click", function () { showFrame(0); });
    $("rwPrev").addEventListener("click", function () { showFrame(st.decodeIndex - 1); });
    $("rwNext").addEventListener("click", function () { showFrame(st.decodeIndex + 1); });
    $("rwLast").addEventListener("click", function () { showFrame(st.numFrames - 1); });
    $("rwJump").addEventListener("change", function () {
      var v = parseInt(this.value, 10); if (!isNaN(v)) showFrame(v);
    });
    $("rwGotoPreview").addEventListener("click", function () {
      if (st.selMb == null) return;
      // 切到帧预览标签并高亮
      var tab = document.querySelector('.tab[data-tab="preview"]');
      if (tab) tab.click();
      if (global.Preview && global.Preview.highlightMb)
        global.Preview.highlightMb(st.decodeIndex, st.selMb);
    });
  }

  global.RawView = {
    init: init, load: load,
    isLoaded: function () { return !!st.rawmap; },
    showMbFromPreview: showMbFromPreview,
  };
})(window);
