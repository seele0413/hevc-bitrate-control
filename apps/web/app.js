const els = {
  streamForm: document.querySelector("#streamForm"),
  rtspInput: document.querySelector("#rtspInput"),
  startBtn: document.querySelector("#startBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  stage: document.querySelector("#stage"),
  divider: document.querySelector("#divider"),
  savingBadge: document.querySelector("#savingBadge"),
  emptyState: document.querySelector("#emptyState"),
  h264Video: document.querySelector("#h264Video"),
  h265Video: document.querySelector("#h265Video"),
  h264Resolution: document.querySelector("#h264Resolution"),
  h265Resolution: document.querySelector("#h265Resolution"),
  h264Bitrate: document.querySelector("#h264Bitrate"),
  h265Bitrate: document.querySelector("#h265Bitrate"),
  h264Crf: document.querySelector("#h264Crf"),
  h265Crf: document.querySelector("#h265Crf"),
  h264Latency: document.querySelector("#h264Latency"),
  h265Latency: document.querySelector("#h265Latency"),
  controls: document.querySelector("#controls"),
  playBtn: document.querySelector("#playBtn"),
  liveChip: document.querySelector("#liveChip"),
  timeLabel: document.querySelector("#timeLabel"),
  streamStatus: document.querySelector("#streamStatus"),
  maskedUrl: document.querySelector("#maskedUrl"),
  outputStatus: document.querySelector("#outputStatus"),
  errorText: document.querySelector("#errorText"),
  runtimeError: document.querySelector("#runtimeError"),
};

const players = {
  h264_native: null,
  h265_optimized: null,
};

const STARTUP_BUFFER_SECONDS = 5;
const SOFT_SYNC_THRESHOLD_SECONDS = 0.08;
const HARD_SYNC_THRESHOLD_SECONDS = 3;
const SLOW_PLAYBACK_RATE = 0.98;
const FAST_PLAYBACK_RATE = 1.02;

let dragging = false;
let streamId = null;
let pollTimer = null;
let latencyTimer = null;
let heartbeatTimer = null;
let h264PlaylistUrl = null;
let h265PlaylistUrl = null;
let latestPayload = null;
let playbackReady = false;
let playbackStarted = false;

function setSplit(pct) {
  const next = Math.max(0, Math.min(100, pct));
  els.h265Video.style.clipPath = `inset(0 0 0 ${next}%)`;
  els.divider.style.left = `${next}%`;
  els.stage.style.setProperty("--p", next);
  els.divider.setAttribute("aria-valuenow", Math.round(next));
}

function posFromEvent(clientX) {
  const rect = els.stage.getBoundingClientRect();
  return ((clientX - rect.left) / rect.width) * 100;
}

function setBusy(busy) {
  els.startBtn.disabled = busy;
  els.rtspInput.disabled = busy && Boolean(streamId);
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

function metricNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatBitrate(value) {
  const number = metricNumber(value);
  return Number.isFinite(number) ? `${number.toFixed(2)} Mbps` : "--";
}

function formatLatency(value) {
  return Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} s` : "--";
}

function resolutionFromProbe(probe) {
  return probe && probe.width && probe.height ? `${probe.width}x${probe.height}` : "--";
}

function output(payload, variant) {
  return payload.outputs?.[variant] || {};
}

function probe(payload, variant) {
  return payload.probes?.[variant] || output(payload, variant).probe || {};
}

function metrics(payload, variant) {
  return output(payload, variant).metrics || {};
}

function outputError(payload) {
  const errors = [];
  for (const variant of ["h264_native", "h265_optimized"]) {
    const item = output(payload, variant);
    if (item.error) errors.push(`${variant}: ${item.error}`);
    if (item.metric_error) errors.push(`${variant} metric: ${item.metric_error}`);
  }
  if (payload.last_error) errors.push(payload.last_error);
  return errors[0] || "--";
}

function outputStatus(payload) {
  const h264 = output(payload, "h264_native").status || "--";
  const h265 = output(payload, "h265_optimized").status || "--";
  return `H.264 ${h264} / H.265 ${h265}`;
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
  els.h264Latency.textContent = formatLatency(playerLatency(els.h264Video, "h264_native"));
  els.h265Latency.textContent = formatLatency(playerLatency(els.h265Video, "h265_optimized"));
}

function playerTimeline(video) {
  if (!video.seekable || video.seekable.length === 0) return null;
  const index = video.seekable.length - 1;
  const start = video.seekable.start(index);
  const end = video.seekable.end(index);
  return Number.isFinite(start) && Number.isFinite(end) ? { start, end } : null;
}

function maybeStartBufferedPlayback() {
  if (playbackReady) return true;
  const videos = [els.h264Video, els.h265Video];
  const timelines = videos.map(playerTimeline);
  if (timelines.some((timeline) => !timeline)) return false;
  const commonStart = Math.max(...timelines.map((timeline) => timeline.start));
  const commonEnd = Math.min(...timelines.map((timeline) => timeline.end));
  if (commonEnd - commonStart < STARTUP_BUFFER_SECONDS) return false;
  const target = commonEnd - STARTUP_BUFFER_SECONDS;
  videos.forEach((video) => {
    video.currentTime = target;
    video.playbackRate = 1;
  });
  playbackReady = true;
  els.playBtn.disabled = false;
  if (!playbackStarted) playBoth();
  return true;
}

function syncPlayers() {
  if (!playbackReady && !maybeStartBufferedPlayback()) return;
  const videos = [els.h264Video, els.h265Video];
  if (videos.some((video) => video.paused)) return;
  const timelines = videos.map(playerTimeline);
  if (timelines.some((timeline) => !timeline)) return;
  const delta = videos[0].currentTime - videos[1].currentTime;
  if (!Number.isFinite(delta)) return;

  if (Math.abs(delta) > HARD_SYNC_THRESHOLD_SECONDS) {
    const commonStart = Math.max(...timelines.map((timeline) => timeline.start));
    const commonEnd = Math.min(...timelines.map((timeline) => timeline.end));
    const target = Math.max(commonStart, Math.min(...videos.map((video) => video.currentTime)));
    if (target <= commonEnd) {
      videos.forEach((video) => {
        video.currentTime = target;
        video.playbackRate = 1;
      });
    }
    return;
  }

  if (Math.abs(delta) <= SOFT_SYNC_THRESHOLD_SECONDS) {
    videos.forEach((video) => { video.playbackRate = 1; });
  } else if (delta > 0) {
    videos[0].playbackRate = SLOW_PLAYBACK_RATE;
    videos[1].playbackRate = FAST_PLAYBACK_RATE;
  } else {
    videos[0].playbackRate = FAST_PLAYBACK_RATE;
    videos[1].playbackRate = SLOW_PLAYBACK_RATE;
  }
}

function updateSaving(payload) {
  const saving = metricNumber(payload.bandwidth_saving_pct);
  els.savingBadge.textContent = Number.isFinite(saving) ? `${saving.toFixed(0)}%` : "--";
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
      liveSyncDurationCount: 5,
      maxLiveSyncPlaybackRate: FAST_PLAYBACK_RATE,
      maxBufferLength: 20,
      backBufferLength: 20,
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
  h264PlaylistUrl = null;
  h265PlaylistUrl = null;
  latestPayload = null;
  playbackReady = false;
  playbackStarted = false;
  for (const video of [els.h264Video, els.h265Video]) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  els.emptyState.classList.remove("hide");
  els.stage.classList.remove("active");
  els.controls.classList.remove("playing");
  els.playBtn.disabled = true;
  els.h264Bitrate.textContent = "--";
  els.h265Bitrate.textContent = "--";
  els.h264Latency.textContent = "--";
  els.h265Latency.textContent = "--";
  els.savingBadge.textContent = "--";
}

function playBoth() {
  if (!playbackReady) return;
  playbackStarted = true;
  els.h264Video.play().catch(() => {});
  els.h265Video.play().catch(() => {});
  els.controls.classList.add("playing");
}

function pauseBoth() {
  els.h264Video.pause();
  els.h265Video.pause();
  els.controls.classList.remove("playing");
  for (const video of [els.h264Video, els.h265Video]) video.playbackRate = 1;
}

function updateStatus(payload) {
  latestPayload = payload;
  const h264Probe = probe(payload, "h264_native");
  const h265Probe = probe(payload, "h265_optimized");
  const h264Metrics = metrics(payload, "h264_native");
  const h265Metrics = metrics(payload, "h265_optimized");

  els.streamStatus.textContent = payload.status || "unknown";
  els.maskedUrl.textContent = payload.masked_url || "--";
  els.outputStatus.textContent = outputStatus(payload);
  els.errorText.textContent = outputError(payload);
  els.h264Resolution.textContent = resolutionFromProbe(h264Probe);
  els.h265Resolution.textContent = resolutionFromProbe(h265Probe);
  els.h264Bitrate.textContent = formatBitrate(h264Metrics.native_bitrate_mbps);
  els.h265Bitrate.textContent = formatBitrate(h265Metrics.native_bitrate_mbps);
  els.h264Crf.textContent = h264Probe.crf_label || "原生默认";
  els.h265Crf.textContent = h265Probe.crf_label || "36.0";
  const dropped = metricNumber(payload.dropped_frames) || 0;
  els.timeLabel.textContent = `等价 H.264 预览 · 原生码率统计 · 丢帧 ${dropped}`;
  setLiveChip(payload.status);
  updateLatencyLabels();
  updateSaving(payload);

  if (payload.h264_native_playlist_url && payload.h264_native_playlist_url !== h264PlaylistUrl) {
    attachHls(els.h264Video, payload.h264_native_playlist_url, "h264_native");
    h264PlaylistUrl = payload.h264_native_playlist_url;
  }
  if (
    payload.h265_optimized_playlist_url &&
    payload.h265_optimized_playlist_url !== h265PlaylistUrl
  ) {
    attachHls(els.h265Video, payload.h265_optimized_playlist_url, "h265_optimized");
    h265PlaylistUrl = payload.h265_optimized_playlist_url;
  }
  if (h264PlaylistUrl || h265PlaylistUrl) {
    els.emptyState.classList.add("hide");
    els.stage.classList.add("active");
  }
  if (h264PlaylistUrl && h265PlaylistUrl) {
    maybeStartBufferedPlayback();
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
      updateStatus(await fetchJson(`/api/streams/${streamId}`, { cache: "no-store" }));
    } catch (error) {
      els.errorText.textContent = error.message;
      setLiveChip("failed");
    }
  }, 1200);
  latencyTimer = window.setInterval(() => {
    syncPlayers();
    updateLatencyLabels();
    if (latestPayload) updateSaving(latestPayload);
  }, 500);
  heartbeatTimer = window.setInterval(async () => {
    if (!streamId) return;
    try {
      await fetchJson(`/api/streams/${streamId}/heartbeat`, {
        method: "POST",
        cache: "no-store",
      });
    } catch (error) {
      els.errorText.textContent = error.message;
    }
  }, 2000);
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
  if (heartbeatTimer) {
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

async function checkRuntime() {
  try {
    const runtime = await fetchJson("/api/runtime", { cache: "no-store" });
    const variants = runtime.live_preview?.variants || [];
    if (
      runtime.pipeline_version !== "v1.8.0" ||
      runtime.live_preview?.frontend !== "apps/web" ||
      runtime.live_preview?.preview_codec !== "h264" ||
      !variants.includes("h264_native") ||
      !variants.includes("h265_optimized")
    ) {
      throw new Error("当前后端不是 V1.6 实时双路编码入口");
    }
    els.runtimeError.hidden = true;
    els.runtimeError.textContent = "";
    return true;
  } catch (error) {
    els.runtimeError.hidden = false;
    els.runtimeError.textContent = error.message || "后端服务还没启动";
    return false;
  }
}

async function startStream(rtspUrl) {
  if (!(await checkRuntime())) return;
  detachPlayers();
  setBusy(true);
  setLiveChip("starting");
  els.streamStatus.textContent = "starting";
  els.maskedUrl.textContent = "--";
  els.outputStatus.textContent = "--";
  els.errorText.textContent = "--";
  try {
    const payload = await fetchJson("/api/streams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rtsp_url: rtspUrl }),
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
    updateStatus(await fetchJson(`/api/streams/${id}`, { method: "DELETE" }));
  } catch (error) {
    els.errorText.textContent = error.message;
    setLiveChip("failed");
  } finally {
    detachPlayers();
    els.rtspInput.disabled = false;
  }
}

els.divider.addEventListener("mousedown", (event) => {
  dragging = true;
  event.preventDefault();
});
window.addEventListener("mousemove", (event) => {
  if (dragging) setSplit(posFromEvent(event.clientX));
});
window.addEventListener("mouseup", () => {
  dragging = false;
});
els.divider.addEventListener("touchstart", () => {
  dragging = true;
}, { passive: true });
els.divider.addEventListener("touchmove", (event) => {
  if (dragging) setSplit(posFromEvent(event.touches[0].clientX));
}, { passive: true });
window.addEventListener("touchend", () => {
  dragging = false;
});
els.divider.addEventListener("keydown", (event) => {
  const current = Number.parseFloat(els.divider.style.left) || 50;
  if (event.key === "ArrowLeft") {
    setSplit(current - 2);
    event.preventDefault();
  }
  if (event.key === "ArrowRight") {
    setSplit(current + 2);
    event.preventDefault();
  }
});
els.streamForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const rtspUrl = els.rtspInput.value.trim();
  if (rtspUrl) startStream(rtspUrl);
});
els.stopBtn.addEventListener("click", stopStream);
els.playBtn.addEventListener("click", () => {
  if (els.h264Video.paused && els.h265Video.paused) playBoth();
  else pauseBoth();
});
els.h264Video.addEventListener("pause", () => {
  if (!els.h265Video.paused) pauseBoth();
});
els.h264Video.addEventListener("play", () => {
  els.controls.classList.add("playing");
});
window.addEventListener("pagehide", () => {
  if (!streamId) return;
  const id = streamId;
  streamId = null;
  stopPolling();
  navigator.sendBeacon(`/api/streams/${id}/stop`, new Blob([], { type: "text/plain" }));
});

setSplit(Number.parseFloat(new URLSearchParams(location.search).get("p")) || 50);
setLiveChip("idle");
checkRuntime();
