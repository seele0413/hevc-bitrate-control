const els = {
  tabs: document.querySelectorAll(".strategy-tab"),
  viewerGeneric: document.querySelector("#viewerGeneric"),
  viewerSpecialized: document.querySelector("#viewerSpecialized"),
  streamForm: document.querySelector("#streamForm"),
  sourceRtspInput: document.querySelector("#sourceRtspInput"),
  conservativeRtspInput: document.querySelector("#conservativeRtspInput"),
  startBtn: document.querySelector("#startBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  stage: document.querySelector("#stage"),
  divider: document.querySelector("#divider"),
  splitBadge: document.querySelector("#splitBadge"),
  sourceVideo: document.querySelector("#sourceVideo"),
  conservativeVideo: document.querySelector("#conservativeVideo"),
  sourceCodec: document.querySelector("#sourceCodec"),
  sourceResolution: document.querySelector("#sourceResolution"),
  sourceBitrate: document.querySelector("#sourceBitrate"),
  sourceLatency: document.querySelector("#sourceLatency"),
  sourceStatus: document.querySelector("#sourceStatus"),
  conservativeCodec: document.querySelector("#conservativeCodec"),
  conservativePreviewMode: document.querySelector("#conservativePreviewMode"),
  conservativeBitrate: document.querySelector("#conservativeBitrate"),
  conservativeLatency: document.querySelector("#conservativeLatency"),
  conservativeStatus: document.querySelector("#conservativeStatus"),
  controls: document.querySelector("#controls"),
  playBtn: document.querySelector("#playBtn"),
  liveChip: document.querySelector("#liveChip"),
  timeLabel: document.querySelector("#timeLabel"),
  streamStatus: document.querySelector("#streamStatus"),
  maskedUrl: document.querySelector("#maskedUrl"),
  probeText: document.querySelector("#probeText"),
  errorText: document.querySelector("#errorText"),
};

const players = {
  source: null,
  conservative: null,
};

let dragging = false;
let streamId = null;
let pollTimer = null;
let latencyTimer = null;
let currentSourceUrl = null;
let currentConservativeUrl = null;
let latestPayload = null;

function setSplit(pct) {
  const next = Math.max(0, Math.min(100, pct));
  els.conservativeVideo.style.clipPath = `inset(0 0 0 ${next}%)`;
  els.divider.style.left = `${next}%`;
  els.stage.style.setProperty("--p", next);
  els.divider.setAttribute("aria-valuenow", Math.round(next));
}

function posFromEvent(clientX) {
  const rect = els.stage.getBoundingClientRect();
  return ((clientX - rect.left) / rect.width) * 100;
}

function showTab(which) {
  const generic = which === "generic";
  els.viewerGeneric.hidden = !generic;
  els.viewerSpecialized.hidden = generic;
  els.tabs.forEach((tab) => {
    const active = tab.dataset.target === which;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  if (!generic) pauseBoth();
}

function setBusy(busy) {
  els.startBtn.disabled = busy;
  els.sourceRtspInput.disabled = busy && Boolean(streamId);
  els.conservativeRtspInput.disabled = busy && Boolean(streamId);
  els.stopBtn.disabled = !streamId;
}

function setLiveChip(status) {
  els.liveChip.classList.remove("running", "failed");
  if (status === "running") {
    els.liveChip.textContent = "实时预览中";
    els.liveChip.classList.add("running");
  } else if (status === "failed") {
    els.liveChip.textContent = "拉流失败";
    els.liveChip.classList.add("failed");
  } else if (status === "starting") {
    els.liveChip.textContent = "启动中";
  } else if (status === "stopped") {
    els.liveChip.textContent = "已停止";
  } else {
    els.liveChip.textContent = "未连接";
  }
}

function formatProbe(probe) {
  if (!probe || !probe.ok) return "--";
  const resolution = probe.width && probe.height ? `${probe.width}x${probe.height}` : "--";
  const fps = Number.isFinite(probe.fps) ? `${probe.fps.toFixed(2)}fps` : "--";
  const encoded = probe.already_encoded ? "已编码" : "未编码";
  return `${probe.codec || "--"} · ${resolution} · ${fps} · ${encoded}`;
}

function outputProbe(payload, variant) {
  return payload.probes?.[variant] || payload.outputs?.[variant]?.probe || {};
}

function outputStatus(payload, variant) {
  return payload.outputs?.[variant]?.status || payload.status || "--";
}

function outputError(payload) {
  const errors = [];
  for (const variant of ["source", "conservative"]) {
    const err = payload.outputs?.[variant]?.error;
    if (err) errors.push(`${variant}: ${err}`);
  }
  if (payload.last_error) errors.push(payload.last_error);
  return errors[0] || "--";
}

function formatBitrate(value) {
  return Number.isFinite(value) ? `${value.toFixed(2)} Mbps` : "--";
}

function formatLatency(value) {
  return Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} s` : "--";
}

function outputMetrics(payload, variant) {
  return payload.outputs?.[variant]?.metrics || {};
}

function metricNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function bitrateSaving(sourceMbps, conservativeMbps) {
  const source = metricNumber(sourceMbps);
  const conservative = metricNumber(conservativeMbps);
  if (!Number.isFinite(source) || source <= 0 || !Number.isFinite(conservative)) {
    return null;
  }
  return ((source - conservative) / source) * 100;
}

function playerLatency(video, key) {
  const player = players[key];
  if (player && Number.isFinite(player.latency)) {
    return player.latency;
  }
  const seekable = video.seekable;
  if (!seekable || seekable.length <= 0) return null;
  const liveEdge = seekable.end(seekable.length - 1);
  if (!Number.isFinite(liveEdge)) return null;
  return Math.max(0, liveEdge - video.currentTime);
}

function updateLatencyLabels() {
  els.sourceLatency.textContent = formatLatency(playerLatency(els.sourceVideo, "source"));
  els.conservativeLatency.textContent = formatLatency(
    playerLatency(els.conservativeVideo, "conservative"),
  );
}

function updateSavingBadge(payload) {
  const saving = metricNumber(payload.bandwidth_saving_pct) ??
    bitrateSaving(
      outputMetrics(payload, "source").camera_bitrate_mbps,
      outputMetrics(payload, "conservative").camera_bitrate_mbps,
    );
  els.splitBadge.textContent = Number.isFinite(saving) ? `${saving.toFixed(0)}%` : "--";
}

function attachHls(video, url, key) {
  if (!url) return;
  if (players[key]) {
    players[key].destroy();
    players[key] = null;
  }
  if (window.Hls && window.Hls.isSupported()) {
    const player = new window.Hls({
      lowLatencyMode: false,
      liveSyncDurationCount: 3,
      maxLiveSyncPlaybackRate: 1.2,
    });
    player.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data || !data.fatal) return;
      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        player.startLoad();
        return;
      }
      if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        player.recoverMediaError();
        return;
      }
      player.destroy();
      players[key] = null;
    });
    player.loadSource(url);
    player.attachMedia(video);
    players[key] = player;
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
  } else {
    throw new Error("当前浏览器不支持 HLS 播放");
  }
}

function detachPlayers() {
  Object.keys(players).forEach((key) => {
    if (players[key]) {
      players[key].destroy();
      players[key] = null;
    }
  });
  currentSourceUrl = null;
  currentConservativeUrl = null;
  latestPayload = null;
  for (const video of [els.sourceVideo, els.conservativeVideo]) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  els.controls.classList.remove("playing");
  els.playBtn.disabled = true;
  els.sourceBitrate.textContent = "--";
  els.sourceLatency.textContent = "--";
  els.conservativeBitrate.textContent = "--";
  els.conservativeLatency.textContent = "--";
  els.conservativeCodec.textContent = "--";
  els.conservativePreviewMode.textContent = "H.264 HLS";
  els.splitBadge.textContent = "--";
}

function playBoth() {
  els.sourceVideo.play().catch(() => {});
  els.conservativeVideo.play().catch(() => {});
  els.controls.classList.add("playing");
}

function pauseBoth() {
  els.sourceVideo.pause();
  els.conservativeVideo.pause();
  els.controls.classList.remove("playing");
}

function updateStatus(payload) {
  latestPayload = payload;
  const sourceProbe = outputProbe(payload, "source");
  const conservativeProbe = outputProbe(payload, "conservative");
  const resolution = sourceProbe.width && sourceProbe.height ?
    `${sourceProbe.width}x${sourceProbe.height}` : "--";
  const sourceMetrics = outputMetrics(payload, "source");
  const conservativeMetrics = outputMetrics(payload, "conservative");
  const conservativePreviewMode = payload.outputs?.conservative?.preview_mode;
  els.streamStatus.textContent = payload.status || "unknown";
  els.maskedUrl.textContent = payload.masked_url || "--";
  els.probeText.textContent =
    `source ${formatProbe(sourceProbe)} / conservative ${formatProbe(conservativeProbe)}`;
  els.errorText.textContent = outputError(payload);
  els.sourceCodec.textContent = sourceProbe.codec || "--";
  els.sourceResolution.textContent = resolution;
  els.sourceBitrate.textContent = formatBitrate(metricNumber(sourceMetrics.camera_bitrate_mbps));
  els.conservativeCodec.textContent = conservativeProbe.codec || "--";
  els.conservativePreviewMode.textContent =
    conservativePreviewMode === "copy" ? "copy HLS" : "H.264 HLS";
  els.conservativeBitrate.textContent = formatBitrate(
    metricNumber(conservativeMetrics.camera_bitrate_mbps),
  );
  els.sourceStatus.textContent = outputStatus(payload, "source");
  els.conservativeStatus.textContent = outputStatus(payload, "conservative");
  els.timeLabel.textContent =
    `HLS: source ${outputStatus(payload, "source")} / conservative ${outputStatus(payload, "conservative")}`;
  setLiveChip(payload.status);
  updateLatencyLabels();
  updateSavingBadge(payload);

  if (payload.source_playlist_url && payload.source_playlist_url !== currentSourceUrl) {
    attachHls(els.sourceVideo, payload.source_playlist_url, "source");
    currentSourceUrl = payload.source_playlist_url;
  }
  if (
    payload.conservative_playlist_url &&
    payload.conservative_playlist_url !== currentConservativeUrl
  ) {
    attachHls(els.conservativeVideo, payload.conservative_playlist_url, "conservative");
    currentConservativeUrl = payload.conservative_playlist_url;
  }
  if (currentSourceUrl && currentConservativeUrl) {
    els.playBtn.disabled = false;
    playBoth();
  }
  if (payload.status === "failed" || payload.status === "stopped") {
    stopPolling();
    setBusy(false);
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败：${response.status}`);
  }
  return payload;
}

function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(async () => {
    if (!streamId) return;
    try {
      const payload = await fetchJson(`/api/streams/${streamId}`, { cache: "no-store" });
      updateStatus(payload);
    } catch (error) {
      els.errorText.textContent = error.message;
      setLiveChip("failed");
    }
  }, 1500);
  latencyTimer = window.setInterval(() => {
    updateLatencyLabels();
    if (latestPayload) updateSavingBadge(latestPayload);
  }, 500);
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  if (latencyTimer) {
    window.clearInterval(latencyTimer);
    latencyTimer = null;
  }
}

async function startStream(sourceRtspUrl, conservativeRtspUrl) {
  detachPlayers();
  setBusy(true);
  setLiveChip("starting");
  els.streamStatus.textContent = "starting";
  els.maskedUrl.textContent = "--";
  els.probeText.textContent = "--";
  els.errorText.textContent = "--";
  els.sourceStatus.textContent = "starting";
  els.conservativeStatus.textContent = "starting";
  try {
    const payload = await fetchJson("/api/streams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_rtsp_url: sourceRtspUrl,
        conservative_rtsp_url: conservativeRtspUrl,
      }),
    });
    streamId = payload.stream_id;
    setBusy(false);
    updateStatus(payload);
    startPolling();
  } catch (error) {
    streamId = null;
    els.errorText.textContent = error.message;
    setLiveChip("failed");
    setBusy(false);
  }
}

async function stopStream() {
  if (!streamId) return;
  const id = streamId;
  stopPolling();
  streamId = null;
  setBusy(false);
  try {
    const payload = await fetchJson(`/api/streams/${id}`, { method: "DELETE" });
    updateStatus(payload);
  } catch (error) {
    els.errorText.textContent = error.message;
    setLiveChip("failed");
  } finally {
    detachPlayers();
    els.sourceRtspInput.disabled = false;
    els.conservativeRtspInput.disabled = false;
    els.sourceStatus.textContent = "已停止";
    els.conservativeStatus.textContent = "已停止";
  }
}

els.tabs.forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.target)));
els.divider.addEventListener("mousedown", (e) => {
  dragging = true;
  e.preventDefault();
});
window.addEventListener("mousemove", (e) => {
  if (dragging) setSplit(posFromEvent(e.clientX));
});
window.addEventListener("mouseup", () => {
  dragging = false;
});
els.divider.addEventListener("touchstart", () => {
  dragging = true;
}, { passive: true });
els.divider.addEventListener("touchmove", (e) => {
  if (dragging) setSplit(posFromEvent(e.touches[0].clientX));
}, { passive: true });
window.addEventListener("touchend", () => {
  dragging = false;
});
els.divider.addEventListener("keydown", (e) => {
  const current = Number.parseFloat(els.divider.style.left) || 50;
  if (e.key === "ArrowLeft") {
    setSplit(current - 2);
    e.preventDefault();
  }
  if (e.key === "ArrowRight") {
    setSplit(current + 2);
    e.preventDefault();
  }
});
els.streamForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const sourceRtspUrl = els.sourceRtspInput.value.trim();
  const conservativeRtspUrl = els.conservativeRtspInput.value.trim();
  if (sourceRtspUrl && conservativeRtspUrl) {
    startStream(sourceRtspUrl, conservativeRtspUrl);
  }
});
els.stopBtn.addEventListener("click", stopStream);
els.playBtn.addEventListener("click", () => {
  if (els.sourceVideo.paused && els.conservativeVideo.paused) playBoth();
  else pauseBoth();
});
els.sourceVideo.addEventListener("pause", () => {
  if (!els.conservativeVideo.paused) pauseBoth();
});
els.sourceVideo.addEventListener("play", () => {
  els.controls.classList.add("playing");
});

setSplit(Number.parseFloat(new URLSearchParams(location.search).get("p")) || 50);
showTab(new URLSearchParams(location.search).get("tab") === "specialized" ? "specialized" : "generic");
