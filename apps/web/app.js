const elements = {
  fileInput: document.querySelector("#videoFile"),
  fileName: document.querySelector("#fileName"),
  createJob: document.querySelector("#createJob"),
  statusBadge: document.querySelector("#statusBadge"),
  statusText: document.querySelector("#statusText"),
  progressFill: document.querySelector("#progressFill"),
  errorText: document.querySelector("#errorText"),
  resultsPanel: document.querySelector("#resultsPanel"),
  modeTabs: document.querySelector("#modeTabs"),
  compareStage: document.querySelector("#compareStage"),
  splitSlider: document.querySelector("#splitSlider"),
  topLayer: document.querySelector("#topLayer"),
  divider: document.querySelector("#divider"),
  savingBadge: document.querySelector("#savingBadge"),
  selectedLabel: document.querySelector("#selectedLabel"),
  selectedSubLabel: document.querySelector("#selectedSubLabel"),
  baseVideo: document.querySelector("#baseVideo"),
  overlayVideo: document.querySelector("#overlayVideo"),
  playToggle: document.querySelector("#playToggle"),
  seekSlider: document.querySelector("#seekSlider"),
  timeText: document.querySelector("#timeText"),
  speedSelect: document.querySelector("#speedSelect"),
  audioSelect: document.querySelector("#audioSelect"),
  metricGrid: document.querySelector("#metricGrid"),
  downloadGrid: document.querySelector("#downloadGrid"),
};

const statusTitles = {
  queued: "排队中",
  preparing_reference: "准备参考画面",
  encoding_default: "编码默认方案",
  searching_conservative: "搜索保守综合策略",
  validating_conservative: "验证保守综合策略",
  searching_balanced: "搜索均衡综合策略",
  validating_balanced: "验证均衡综合策略",
  searching_aggressive: "搜索激进综合策略",
  validating_aggressive: "验证激进综合策略",
  generating_previews: "生成浏览器 H.264 预览",
  completed: "已完成",
  failed: "失败",
};

const modeMeta = {
  composite_conservative: {
    title: "保守",
    mode: "conservative",
    quality: "QUALITY 95",
    detail: "WZ265保守综合策略",
  },
  composite_balanced: {
    title: "均衡",
    mode: "balanced",
    quality: "QUALITY 90",
    detail: "WZ265均衡综合策略",
  },
  composite_aggressive: {
    title: "激进",
    mode: "aggressive",
    quality: "QUALITY 83",
    detail: "WZ265激进综合策略",
  },
};

let pollTimer = null;
let activeResult = null;
let activeStrategyId = "composite_balanced";
let userSeeking = false;

function setStatus(job) {
  const title = job.stage_title || statusTitles[job.status] || job.status;
  elements.statusBadge.textContent = title;
  elements.statusText.textContent = job.job_id
    ? `任务 ${job.job_id.slice(0, 8)} 正在处理。`
    : "选择一个本地视频后创建任务。";
  elements.progressFill.style.width = `${job.progress || 0}%`;
  if (job.status === "failed" && job.error) {
    elements.errorText.hidden = false;
    elements.errorText.textContent = job.error;
  } else {
    elements.errorText.hidden = true;
    elements.errorText.textContent = "";
  }
}

function setPlainStatus(text) {
  elements.statusBadge.textContent = "提示";
  elements.statusText.textContent = text;
  elements.progressFill.style.width = "0%";
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

function savingClass(value) {
  if (value > 0) return "saving-positive";
  if (value < 0) return "saving-negative";
  return "";
}

function byId(id) {
  return activeResult.strategies.find((strategy) => strategy.strategy_id === id);
}

function setSplit(value) {
  const percent = `${value}%`;
  elements.compareStage.style.setProperty("--split", percent);
}

function renderModeTabs() {
  elements.modeTabs.innerHTML = "";
  Object.entries(modeMeta).forEach(([strategyId, meta]) => {
    const strategy = byId(strategyId);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `mode-tab${strategyId === activeStrategyId ? " active" : ""}`;
    button.disabled = !strategy || strategy.status !== "completed";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", strategyId === activeStrategyId ? "true" : "false");
    button.innerHTML = `
      <span class="mode-kicker">${meta.quality}</span>
      <span class="mode-title">${meta.title}</span>
      <span class="mode-detail">${meta.detail}</span>
    `;
    button.addEventListener("click", () => {
      activeStrategyId = strategyId;
      renderResult();
    });
    elements.modeTabs.appendChild(button);
  });
}

function setVideoSources(defaultStrategy, selectedStrategy) {
  const baseSrc = defaultStrategy.preview_url || "";
  const overlaySrc = selectedStrategy.preview_url || "";
  if (elements.baseVideo.dataset.src !== baseSrc) {
    elements.baseVideo.src = baseSrc;
    elements.baseVideo.dataset.src = baseSrc;
  }
  if (elements.overlayVideo.dataset.src !== overlaySrc) {
    elements.overlayVideo.src = overlaySrc;
    elements.overlayVideo.dataset.src = overlaySrc;
  }
  elements.baseVideo.muted = elements.audioSelect.value !== "default";
  elements.overlayVideo.muted = elements.audioSelect.value !== "selected";
  applyPlaybackRate();
}

function renderCompare() {
  const defaultStrategy = byId("default_x265");
  let selectedStrategy = byId(activeStrategyId);
  if (!selectedStrategy || selectedStrategy.status !== "completed") {
    selectedStrategy = activeResult.strategies.find(
      (item) => item.strategy_id !== "default_x265" && item.status === "completed",
    );
    activeStrategyId = selectedStrategy ? selectedStrategy.strategy_id : activeStrategyId;
  }
  if (!defaultStrategy || !selectedStrategy) return;
  const meta = modeMeta[selectedStrategy.strategy_id] || {
    title: selectedStrategy.title,
    detail: "综合策略",
  };
  elements.selectedLabel.textContent = meta.title;
  elements.selectedSubLabel.textContent = selectedStrategy.title || meta.detail;
  elements.savingBadge.textContent =
    selectedStrategy.saving_vs_default_pct === null ||
    selectedStrategy.saving_vs_default_pct === undefined
      ? "--"
      : `${selectedStrategy.saving_vs_default_pct.toFixed(2)}%`;
  elements.savingBadge.className = `saving-badge ${savingClass(
    selectedStrategy.saving_vs_default_pct,
  )}`;
  setVideoSources(defaultStrategy, selectedStrategy);
}

function renderMetrics() {
  elements.metricGrid.innerHTML = "";
  activeResult.strategies.forEach((strategy) => {
    const card = document.createElement("article");
    card.className = "metric-card";
    const saving = strategy.saving_vs_default_pct;
    card.innerHTML = `
      <h3>${strategy.title}</h3>
      <div class="metric-list">
        <div>状态：<span>${strategy.status === "completed" ? "已生成" : "失败"}</span></div>
        <div>分辨率：<span>${strategy.resolution || "--"}</span></div>
        <div>平均视频包码率：<span>${formatBitrate(strategy.average_video_packet_bitrate_bps)}</span></div>
        <div>相对默认：<span class="${savingClass(saving)}">${formatSaving(saving)}</span></div>
      </div>
    `;
    elements.metricGrid.appendChild(card);
  });
}

function renderDownloads() {
  elements.downloadGrid.innerHTML = "";
  activeResult.strategies.forEach((strategy) => {
    const card = document.createElement("article");
    card.className = "download-card";
    const href = strategy.download_url || "";
    card.innerHTML = `
      <h3>${strategy.title}</h3>
      <a href="${href}" ${href ? "" : 'aria-disabled="true"'}>${href ? "下载 H.265 输出" : "未生成文件"}</a>
    `;
    elements.downloadGrid.appendChild(card);
  });
}

function renderResult() {
  if (!activeResult) return;
  elements.resultsPanel.hidden = false;
  renderModeTabs();
  renderCompare();
  renderMetrics();
  renderDownloads();
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `请求失败：${response.status}`);
  }
  return data;
}

async function pollJob(jobId) {
  try {
    const job = await fetchJson(`/api/jobs/${jobId}`);
    setStatus(job);
    if (job.status === "completed") {
      clearInterval(pollTimer);
      pollTimer = null;
      activeResult = await fetchJson(`/api/jobs/${jobId}/results`);
      renderResult();
      return;
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
  const file = elements.fileInput.files[0];
  if (!file) {
    setPlainStatus("请先选择一个 MP4 或 MKV 视频。");
    return;
  }
  elements.createJob.disabled = true;
  elements.resultsPanel.hidden = true;
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
    elements.createJob.disabled = false;
  }
}

function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "00:00";
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60)
    .toString()
    .padStart(2, "0");
  const rest = (whole % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

function mediaDuration() {
  return Math.max(elements.baseVideo.duration || 0, elements.overlayVideo.duration || 0);
}

function alignVideos(force = false) {
  const master = elements.baseVideo;
  const slave = elements.overlayVideo;
  if (!master.src || !slave.src || !Number.isFinite(master.currentTime)) return;
  const drift = Math.abs((slave.currentTime || 0) - master.currentTime);
  if (force || drift > 0.12) {
    slave.currentTime = master.currentTime;
  }
  slave.playbackRate = master.playbackRate;
}

function applyPlaybackRate() {
  const rate = Number(elements.speedSelect.value || 1);
  elements.baseVideo.playbackRate = rate;
  elements.overlayVideo.playbackRate = rate;
}

function applyAudioMode() {
  const mode = elements.audioSelect.value;
  elements.baseVideo.muted = mode !== "default";
  elements.overlayVideo.muted = mode !== "selected";
}

async function playBoth() {
  alignVideos(true);
  applyPlaybackRate();
  applyAudioMode();
  await Promise.allSettled([elements.baseVideo.play(), elements.overlayVideo.play()]);
  elements.playToggle.textContent = "暂停";
}

function pauseBoth() {
  elements.baseVideo.pause();
  elements.overlayVideo.pause();
  elements.playToggle.textContent = "播放";
}

function updateSeekFromVideo() {
  if (userSeeking) return;
  const duration = mediaDuration();
  const current = elements.baseVideo.currentTime || 0;
  elements.seekSlider.value = duration > 0 ? Math.round((current / duration) * 1000) : 0;
  elements.timeText.textContent = `${formatTime(current)} / ${formatTime(duration)}`;
}

elements.fileInput.addEventListener("change", () => {
  const file = elements.fileInput.files[0];
  elements.fileName.textContent = file ? file.name : "选择 MP4 / MKV 视频";
});

elements.createJob.addEventListener("click", createJob);

elements.splitSlider.addEventListener("input", () => {
  setSplit(elements.splitSlider.value);
});

elements.playToggle.addEventListener("click", () => {
  if (elements.baseVideo.paused) {
    playBoth();
  } else {
    pauseBoth();
  }
});

elements.seekSlider.addEventListener("input", () => {
  userSeeking = true;
  const duration = mediaDuration();
  const next = (Number(elements.seekSlider.value) / 1000) * duration;
  elements.baseVideo.currentTime = next;
  elements.overlayVideo.currentTime = next;
  elements.timeText.textContent = `${formatTime(next)} / ${formatTime(duration)}`;
});

elements.seekSlider.addEventListener("change", () => {
  userSeeking = false;
  alignVideos(true);
});

elements.speedSelect.addEventListener("change", applyPlaybackRate);
elements.audioSelect.addEventListener("change", applyAudioMode);
elements.baseVideo.addEventListener("timeupdate", updateSeekFromVideo);
elements.baseVideo.addEventListener("pause", pauseBoth);
elements.baseVideo.addEventListener("ended", pauseBoth);
elements.overlayVideo.addEventListener("pause", () => {
  if (!elements.baseVideo.paused) elements.overlayVideo.play();
});

setInterval(() => {
  if (!elements.baseVideo.paused) alignVideos(false);
}, 500);

setSplit(elements.splitSlider.value);
const lastJobId = localStorage.getItem("hevc_lab_last_job");
if (lastJobId) {
  pollJob(lastJobId);
} else {
  setPlainStatus("选择一个本地视频后创建任务。");
}
