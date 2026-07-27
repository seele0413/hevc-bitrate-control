const els = {
  tabs: document.querySelectorAll(".strategy-tab"),
  viewerGeneric: document.querySelector("#viewerGeneric"),
  viewerSpecialized: document.querySelector("#viewerSpecialized"),
  liveStage: document.querySelector("#liveStage"),
  liveVideo: document.querySelector("#liveVideo"),
  liveEmpty: document.querySelector("#liveEmpty"),
  rtspForm: document.querySelector("#rtspForm"),
  rtspUrl: document.querySelector("#rtspUrl"),
  startStream: document.querySelector("#startStream"),
  stopStream: document.querySelector("#stopStream"),
  sourceText: document.querySelector("#sourceText"),
  hlsText: document.querySelector("#hlsText"),
  statusName: document.querySelector("#statusName"),
  streamText: document.querySelector("#streamText"),
  stateText: document.querySelector("#stateText"),
  liveStatusBadge: document.querySelector("#liveStatusBadge"),
  maskedUrlText: document.querySelector("#maskedUrlText"),
  logText: document.querySelector("#logText"),
  liveErrorText: document.querySelector("#liveErrorText"),
  runtimeError: document.querySelector("#runtimeError"),
  fileInput: document.querySelector("#videoFile"),
  fileName: document.querySelector("#fileName"),
  createJob: document.querySelector("#createJob"),
  statusBadge: document.querySelector("#statusBadge"),
  statusText: document.querySelector("#statusText"),
  progressFill: document.querySelector("#progressFill"),
  errorText: document.querySelector("#errorText"),
  resultsPanel: document.querySelector("#resultsPanel"),
  metricGrid: document.querySelector("#metricGrid"),
  downloadGrid: document.querySelector("#downloadGrid"),
};

const expectedPipelineVersion = "v1.4.0";
const expectedStrategyIds = [
  "default_x265",
  "generic_no_roi",
  "budget_neutral_roi",
  "roi_denoise_experimental",
];

const statusTitles = {
  queued: "排队中",
  preparing_reference: "准备参考画面",
  encoding_default: "编码默认方案",
  searching_general: "搜索通用无 ROI 方案",
  validating_general: "验证通用无 ROI 方案",
  searching_roi: "搜索预算中性 ROI",
  validating_roi: "验证 ROI 预算和重点区域",
  searching_roi_denoise: "搜索 ROI + 降噪实验项",
  validating_roi_denoise: "验证降噪预算和重点区域",
  generating_previews: "生成浏览器预览",
  completed: "已完成",
  failed: "失败",
};

const liveStatusTitles = {
  starting: "启动中",
  running: "预览中",
  failed: "失败",
  stopped: "已停止",
};

let runtimeReady = false;
let liveStreamId = null;
let livePollTimer = null;
let liveHls = null;
let pollTimer = null;
let activeResult = null;

function showTab(which) {
  const generic = which === "generic";
  els.viewerGeneric.hidden = !generic;
  els.viewerSpecialized.hidden = generic;
  els.tabs.forEach((tab) => {
    const active = tab.dataset.target === which;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `请求失败：${response.status}`);
  }
  return data;
}

function runtimeLooksCurrent(runtime) {
  if (!runtime || runtime.pipeline_version !== expectedPipelineVersion) return false;
  const ids = new Set(runtime.strategy_ids || []);
  return expectedStrategyIds.every((id) => ids.has(id));
}

async function checkRuntime() {
  try {
    const runtime = await fetchJson("/api/runtime");
    if (!runtimeLooksCurrent(runtime)) {
      els.createJob.disabled = true;
      els.runtimeError.hidden = false;
      els.runtimeError.textContent =
        `后端仍是 ${runtime.pipeline_version || "旧版"}，请关闭当前服务后重新运行 start_web.cmd。`;
      return false;
    }
    if (!runtime.live_preview || runtime.live_preview.enabled !== true) {
      els.runtimeError.hidden = false;
      els.runtimeError.textContent = "当前后端未启用实时预览接口。";
      return false;
    }
    runtimeReady = true;
    els.createJob.disabled = false;
    els.runtimeError.hidden = true;
    els.runtimeError.textContent = "";
    return true;
  } catch (error) {
    els.createJob.disabled = true;
    els.runtimeError.hidden = false;
    els.runtimeError.textContent = "后端服务还没启动或没有重启，请运行 start_web.cmd。";
    return false;
  }
}

function setLiveStatus(payload = {}) {
  const status = payload.status || "idle";
  const title = liveStatusTitles[status] || "未启动";
  const streamLabel = payload.stream_id ? payload.stream_id.slice(0, 8) : "--";
  const maskedUrl = payload.masked_url || "--";
  const playlistReady = Boolean(payload.playlist_url);
  const lastLog = payload.log_tail && payload.log_tail.length
    ? payload.log_tail[payload.log_tail.length - 1]
    : "--";

  els.statusName.textContent = title;
  els.liveStatusBadge.textContent = title;
  els.streamText.textContent = streamLabel;
  els.stateText.textContent = status;
  els.sourceText.textContent = maskedUrl;
  els.maskedUrlText.textContent = maskedUrl;
  els.hlsText.textContent = playlistReady ? "已生成" : "未生成";
  els.logText.textContent = lastLog;

  if (payload.error) {
    els.liveErrorText.hidden = false;
    els.liveErrorText.textContent = payload.error;
  } else {
    els.liveErrorText.hidden = true;
    els.liveErrorText.textContent = "";
  }

  els.stopStream.disabled = !payload.stream_id || status === "stopped" || status === "failed";
}

function clearLivePlayer() {
  if (liveHls) {
    liveHls.destroy();
    liveHls = null;
  }
  delete els.liveVideo.dataset.playlist;
  els.liveVideo.pause();
  els.liveVideo.removeAttribute("src");
  els.liveVideo.load();
  els.liveEmpty.classList.remove("hide");
  els.hlsText.textContent = "未生成";
}

function attachLivePlayer(playlistUrl) {
  if (!playlistUrl || els.liveVideo.dataset.playlist === playlistUrl) return;
  clearLivePlayer();
  els.liveEmpty.classList.add("hide");
  els.liveVideo.dataset.playlist = playlistUrl;
  if (window.Hls && window.Hls.isSupported()) {
    liveHls = new window.Hls({
      liveSyncDurationCount: 3,
      maxLiveSyncPlaybackRate: 1.5,
    });
    liveHls.loadSource(playlistUrl);
    liveHls.attachMedia(els.liveVideo);
    liveHls.on(window.Hls.Events.MANIFEST_PARSED, () => {
      els.liveVideo.play().catch(() => {});
    });
  } else if (els.liveVideo.canPlayType("application/vnd.apple.mpegurl")) {
    els.liveVideo.src = playlistUrl;
    els.liveVideo.play().catch(() => {});
  } else {
    els.liveErrorText.hidden = false;
    els.liveErrorText.textContent = "当前浏览器缺少 HLS 播放能力，请确认 hls.js 已加载。";
  }
}

async function pollLiveStream() {
  if (!liveStreamId) return;
  try {
    const payload = await fetchJson(`/api/streams/${liveStreamId}`);
    setLiveStatus(payload);
    if (payload.playlist_url && payload.status === "running") {
      attachLivePlayer(payload.playlist_url);
    }
    if (payload.status === "failed" || payload.status === "stopped") {
      if (livePollTimer) clearInterval(livePollTimer);
      livePollTimer = null;
      if (payload.status === "stopped") clearLivePlayer();
    }
  } catch (error) {
    els.liveErrorText.hidden = false;
    els.liveErrorText.textContent = error.message;
    if (livePollTimer) clearInterval(livePollTimer);
    livePollTimer = null;
  }
}

async function startLiveStream() {
  if (!runtimeReady && !(await checkRuntime())) return;
  const rtspUrl = els.rtspUrl.value.trim();
  if (!rtspUrl) {
    els.liveErrorText.hidden = false;
    els.liveErrorText.textContent = "请先输入 RTSP 地址。";
    return;
  }

  els.startStream.disabled = true;
  clearLivePlayer();
  setLiveStatus({ status: "starting" });
  try {
    const payload = await fetchJson("/api/streams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rtsp_url: rtspUrl }),
    });
    liveStreamId = payload.stream_id;
    setLiveStatus(payload);
    if (livePollTimer) clearInterval(livePollTimer);
    livePollTimer = setInterval(pollLiveStream, 1000);
    await pollLiveStream();
  } catch (error) {
    liveStreamId = null;
    setLiveStatus();
    els.liveErrorText.hidden = false;
    els.liveErrorText.textContent = error.message;
  } finally {
    els.startStream.disabled = false;
  }
}

async function stopLiveStream() {
  if (!liveStreamId) return;
  els.stopStream.disabled = true;
  try {
    const payload = await fetchJson(`/api/streams/${liveStreamId}`, {
      method: "DELETE",
    });
    setLiveStatus(payload);
    if (livePollTimer) clearInterval(livePollTimer);
    livePollTimer = null;
    clearLivePlayer();
  } catch (error) {
    els.liveErrorText.hidden = false;
    els.liveErrorText.textContent = error.message;
  }
}

function setStatus(job) {
  const title = job.stage_title || statusTitles[job.status] || job.status;
  els.statusBadge.textContent = title;
  els.statusText.textContent = job.job_id
    ? `任务 ${job.job_id.slice(0, 8)} 正在处理。`
    : "选择一个本地视频后创建任务。";
  els.progressFill.style.width = `${job.progress || 0}%`;
  if (job.status === "failed" && job.error) {
    els.errorText.hidden = false;
    els.errorText.textContent = job.error;
  } else {
    els.errorText.hidden = true;
    els.errorText.textContent = "";
  }
}

function setPlainStatus(text) {
  els.statusBadge.textContent = "提示";
  els.statusText.textContent = text;
  els.progressFill.style.width = "0%";
}

function resultLooksCurrent(result) {
  if (!result || result.pipeline_version !== expectedPipelineVersion) return false;
  const ids = new Set((result.strategies || []).map((strategy) => strategy.strategy_id));
  return expectedStrategyIds.every((id) => ids.has(id));
}

function formatBitrate(value) {
  if (value === null || value === undefined) return "--";
  return `${(value / 1_000_000).toFixed(6)} Mbit/s`;
}

function formatSaving(value) {
  if (value === null || value === undefined) return "基准";
  const fixed = `${value.toFixed(2)}%`;
  if (value > 0) return `${fixed} 码率节省`;
  if (value < 0) return `${fixed} 码率增加`;
  return "0.00% 持平";
}

function formatNumber(value, digits = 3, suffix = "") {
  if (value === null || value === undefined) return "--";
  return `${value.toFixed(digits)}${suffix}`;
}

function savingClass(value) {
  if (value > 0) return "saving-positive";
  if (value < 0) return "saving-negative";
  return "";
}

function renderMetrics() {
  els.metricGrid.innerHTML = "";
  activeResult.strategies.forEach((strategy) => {
    const card = document.createElement("article");
    card.className = "metric-card";
    card.innerHTML = `
      <h3>${strategy.title}</h3>
      <div class="metric-list">
        <div>状态：<span>${strategy.status === "completed" ? "已生成" : "失败"}</span></div>
        <div>分辨率：<span>${strategy.resolution || "--"}</span></div>
        <div>码率：<span>${formatBitrate(strategy.average_video_packet_bitrate_bps)}</span></div>
        <div>CRF：<span>${formatNumber(strategy.selected_crf, 1)}</span></div>
        <div>VMAF / P5：<span>${formatNumber(strategy.vmaf_mean)} / ${formatNumber(strategy.vmaf_p5)}</span></div>
        <div>相对默认：<span class="${savingClass(strategy.saving_vs_default_pct)}">${formatSaving(strategy.saving_vs_default_pct)}</span></div>
      </div>
    `;
    els.metricGrid.appendChild(card);
  });
}

function renderDownloads() {
  els.downloadGrid.innerHTML = "";
  activeResult.strategies.forEach((strategy) => {
    const card = document.createElement("article");
    card.className = "download-card";
    const href = strategy.download_url || "";
    card.innerHTML = `
      <h3>${strategy.title}</h3>
      <a href="${href}" ${href ? "" : 'aria-disabled="true"'}>${href ? "下载 H.265 输出" : "未生成文件"}</a>
    `;
    els.downloadGrid.appendChild(card);
  });
}

function renderResult() {
  if (!activeResult) return;
  els.resultsPanel.hidden = false;
  renderMetrics();
  renderDownloads();
}

async function pollJob(jobId) {
  try {
    const job = await fetchJson(`/api/jobs/${jobId}`);
    setStatus(job);
    if (job.status === "completed") {
      clearInterval(pollTimer);
      pollTimer = null;
      activeResult = await fetchJson(`/api/jobs/${jobId}/results`);
      if (!resultLooksCurrent(activeResult)) {
        localStorage.removeItem("hevc_lab_last_job");
        activeResult = null;
        els.resultsPanel.hidden = true;
        setPlainStatus("检测到上一次任务是旧版结果，已清除；请重新创建 V1.4 任务。");
        return;
      }
      renderResult();
    }
    if (job.status === "failed") {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (error) {
    clearInterval(pollTimer);
    pollTimer = null;
    setPlainStatus(error.message);
  }
}

async function createJob() {
  if (!runtimeReady && !(await checkRuntime())) return;
  const file = els.fileInput.files[0];
  if (!file) {
    setPlainStatus("请先选择一个 MP4 或 MKV 视频。");
    return;
  }
  els.createJob.disabled = true;
  els.resultsPanel.hidden = true;
  activeResult = null;
  try {
    const body = new FormData();
    body.append("file", file);
    const job = await fetchJson("/api/jobs", {
      method: "POST",
      body,
    });
    localStorage.setItem("hevc_lab_last_job", job.job_id);
    setStatus(job);
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollJob(job.job_id), 1000);
    await pollJob(job.job_id);
  } catch (error) {
    setPlainStatus(error.message);
  } finally {
    els.createJob.disabled = false;
  }
}

els.tabs.forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.target)));
els.rtspForm.addEventListener("submit", (event) => {
  event.preventDefault();
  startLiveStream();
});
els.stopStream.addEventListener("click", stopLiveStream);
els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files[0];
  els.fileName.textContent = file ? file.name : "选择 MP4 / MKV 视频";
});
els.createJob.addEventListener("click", createJob);

async function initApp() {
  setLiveStatus();
  const ready = await checkRuntime();
  if (!ready) return;
  const lastJobId = localStorage.getItem("hevc_lab_last_job");
  if (lastJobId) {
    pollJob(lastJobId);
  }
}

initApp();
