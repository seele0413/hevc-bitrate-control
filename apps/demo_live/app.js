const els = {
  tabs: document.querySelectorAll(".strategy-tab"),
  viewerGeneric: document.querySelector("#viewerGeneric"),
  viewerSpecialized: document.querySelector("#viewerSpecialized"),
  streamForm: document.querySelector("#streamForm"),
  rtspInput: document.querySelector("#rtspInput"),
  startBtn: document.querySelector("#startBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  stage: document.querySelector("#stage"),
  divider: document.querySelector("#divider"),
  sourceVideo: document.querySelector("#sourceVideo"),
  conservativeVideo: document.querySelector("#conservativeVideo"),
  sourceCodec: document.querySelector("#sourceCodec"),
  sourceResolution: document.querySelector("#sourceResolution"),
  sourceBitrate: document.querySelector("#sourceBitrate"),
  sourceLatency: document.querySelector("#sourceLatency"),
  sourceStatus: document.querySelector("#sourceStatus"),
  conservativeCodec: document.querySelector("#conservativeCodec"),
  conservativeResolution: document.querySelector("#conservativeResolution"),
  conservativeBitrate: document.querySelector("#conservativeBitrate"),
  conservativeLatency: document.querySelector("#conservativeLatency"),
  conservativeStatus: document.querySelector("#conservativeStatus"),
  splitBadge: document.querySelector("#splitBadge"),
  controls: document.querySelector("#controls"),
  playBtn: document.querySelector("#playBtn"),
  liveChip: document.querySelector("#liveChip"),
  timeLabel: document.querySelector("#timeLabel"),
  streamStatus: document.querySelector("#streamStatus"),
  maskedUrl: document.querySelector("#maskedUrl"),
  probeText: document.querySelector("#probeText"),
  inputBitrate: document.querySelector("#inputBitrate"),
  strategyText: document.querySelector("#strategyText"),
  errorText: document.querySelector("#errorText"),
};

const players = { source: null, conservative: null };
const playlistUrls = { source: null, conservative: null };
let streamId = null;
let pollTimer = null;
let syncTimer = null;
let dragging = false;

const MAX_COMMON_LATENCY_SECONDS = 10;
const TARGET_COMMON_LATENCY_SECONDS = 3;
const HARD_SYNC_DRIFT_SECONDS = 0.35;

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
  els.rtspInput.disabled = Boolean(streamId);
  els.stopBtn.disabled = !streamId;
}

function setLiveChip(status) {
  els.liveChip.classList.remove("running", "failed");
  if (status === "running") {
    els.liveChip.textContent = "双编码实时运行";
    els.liveChip.classList.add("running");
  } else if (status === "failed") {
    els.liveChip.textContent = "编码失败";
    els.liveChip.classList.add("failed");
  } else if (status === "starting") {
    els.liveChip.textContent = "编码与预览预热中";
  } else if (status === "stopped") {
    els.liveChip.textContent = "已停止";
  } else {
    els.liveChip.textContent = "未连接";
  }
}

function formatBitrate(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(3)} Mbps` : "--";
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${number >= 0 ? "" : "-"}${Math.abs(number).toFixed(1)}%`;
}

function formatLatency(value) {
  return Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} s` : "--";
}

function formatProbe(probe) {
  if (!probe || !probe.ok) return "--";
  const resolution = probe.width && probe.height ? `${probe.width}x${probe.height}` : "--";
  const fps = Number(probe.fps);
  const fpsText = Number.isFinite(fps) ? `${fps.toFixed(2)} fps` : "--";
  const profile = probe.profile || "--";
  const refs = Number.isFinite(Number(probe.refs)) ? `refs ${probe.refs}` : "refs ?";
  const bFrames = Number(probe.has_b_frames) > 0 ? "含 B 帧" : "B 帧未知/无";
  return `${probe.codec || "--"} · ${profile} · ${resolution} · ${fpsText} · ${refs} · ${bFrames}`;
}

function formatStrategy(profile) {
  if (!profile || !profile.x265_params) {
    return "medium · CRF 36.0 · ref 6 · B 8 · lookahead 90 · GOP 10s · AQ2";
  }
  return `${profile.preset} · CRF ${profile.crf} · ref ${profile.ref} · B ${profile.bframes} · lookahead ${profile.lookahead} · GOP ${profile.gop_seconds}s · AQ${profile.aq?.aq_mode ?? 2}`;
}

function output(payload, variant) {
  return payload.outputs?.[variant] || {};
}

function encodedBitrate(payload, variant) {
  return output(payload, variant).metrics?.encoded_bitrate_mbps;
}

function seekableLiveEdge(video) {
  if (!video.seekable || video.seekable.length <= 0) return null;
  const edge = video.seekable.end(video.seekable.length - 1);
  return Number.isFinite(edge) ? edge : null;
}

function previewLatency(video, player) {
  if (player && Number.isFinite(player.latency)) return player.latency;
  const edge = seekableLiveEdge(video);
  return edge === null ? null : Math.max(0, edge - video.currentTime);
}

function seekBothToCommonLiveEdge() {
  const sourceEdge = seekableLiveEdge(els.sourceVideo);
  const conservativeEdge = seekableLiveEdge(els.conservativeVideo);
  if (sourceEdge === null || conservativeEdge === null) return;
  const commonEdge = Math.min(sourceEdge, conservativeEdge);
  const target = Math.max(0, commonEdge - TARGET_COMMON_LATENCY_SECONDS);
  els.sourceVideo.currentTime = target;
  els.conservativeVideo.currentTime = target;
}

function synchronizeVideos() {
  const source = els.sourceVideo;
  const conservative = els.conservativeVideo;
  const sourceLatency = previewLatency(source, players.source);
  const conservativeLatency = previewLatency(conservative, players.conservative);
  els.sourceLatency.textContent = formatLatency(sourceLatency);
  els.conservativeLatency.textContent = formatLatency(conservativeLatency);

  if (
    Number.isFinite(sourceLatency) &&
    Number.isFinite(conservativeLatency) &&
    Math.max(sourceLatency, conservativeLatency) > MAX_COMMON_LATENCY_SECONDS &&
    !source.paused
  ) {
    seekBothToCommonLiveEdge();
  }

  if (source.readyState < 2 || conservative.readyState < 2) return;
  const drift = conservative.currentTime - source.currentTime;
  if (Math.abs(drift) > HARD_SYNC_DRIFT_SECONDS) {
    conservative.currentTime = source.currentTime;
  }
  source.playbackRate = 1;
  conservative.playbackRate = Math.abs(drift) > 0.08 ? (drift > 0 ? 0.97 : 1.03) : 1;
  els.timeLabel.textContent = `双路 HLS · 时间差 ${Math.abs(drift).toFixed(3)} s`;
}

function setSplit(value) {
  const percent = Math.max(0, Math.min(100, Number(value)));
  els.stage.style.setProperty("--p", String(percent));
  els.conservativeVideo.style.clipPath = `inset(0 0 0 ${percent}%)`;
  els.divider.style.left = `${percent}%`;
  els.divider.setAttribute("aria-valuenow", String(Math.round(percent)));
}

function positionFromClientX(clientX) {
  const rect = els.stage.getBoundingClientRect();
  return ((clientX - rect.left) / rect.width) * 100;
}

function attachHls(variant, video, url) {
  if (!url || playlistUrls[variant] === url) return;
  detachHls(variant, video);
  playlistUrls[variant] = url;
  if (window.Hls && window.Hls.isSupported()) {
    const player = new window.Hls({
      lowLatencyMode: false,
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 8,
      maxLiveSyncPlaybackRate: 1.25,
      backBufferLength: 20,
      maxBufferLength: 10,
    });
    players[variant] = player;
    player.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data || !data.fatal || players[variant] !== player) return;
      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        player.startLoad();
        return;
      }
      if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        player.recoverMediaError();
        return;
      }
      player.destroy();
      players[variant] = null;
    });
    player.on(window.Hls.Events.MANIFEST_PARSED, () => {
      if (playlistUrls.source && playlistUrls.conservative) playBoth();
    });
    player.loadSource(url);
    player.attachMedia(video);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
  } else {
    throw new Error("当前浏览器不支持 HLS 播放");
  }
}

function detachHls(variant, video) {
  if (players[variant]) {
    players[variant].destroy();
    players[variant] = null;
  }
  playlistUrls[variant] = null;
  video.pause();
  video.removeAttribute("src");
  video.load();
}

function detachPlayers() {
  detachHls("source", els.sourceVideo);
  detachHls("conservative", els.conservativeVideo);
  els.controls.classList.remove("playing");
  els.playBtn.disabled = true;
  els.sourceBitrate.textContent = "--";
  els.conservativeBitrate.textContent = "--";
  els.sourceLatency.textContent = "--";
  els.conservativeLatency.textContent = "--";
  els.splitBadge.textContent = "--";
}

function playBoth() {
  Promise.allSettled([
    els.sourceVideo.play(),
    els.conservativeVideo.play(),
  ]);
  els.controls.classList.add("playing");
}

function pauseBoth() {
  els.sourceVideo.pause();
  els.conservativeVideo.pause();
  els.controls.classList.remove("playing");
}

function updateStatus(payload) {
  const source = output(payload, "source");
  const conservative = output(payload, "conservative");
  const probe = payload.input_probe || payload.probe || {};
  const resolution = probe.width && probe.height ? `${probe.width}x${probe.height}` : "--";

  els.streamStatus.textContent = payload.status || "--";
  els.maskedUrl.textContent = payload.masked_url || "--";
  els.probeText.textContent = formatProbe(probe);
  els.inputBitrate.textContent = formatBitrate(payload.input_metrics?.bitrate_mbps);
  els.strategyText.textContent = formatStrategy(payload.encoding_profile);
  els.errorText.textContent = source.error || conservative.error || payload.last_error || "--";

  els.sourceCodec.textContent = source.probe?.encoder || "libx264";
  els.sourceResolution.textContent = resolution;
  els.sourceBitrate.textContent = formatBitrate(encodedBitrate(payload, "source"));
  els.sourceStatus.textContent = `${source.metric_status || "--"} / ${source.status || "--"}`;

  els.conservativeCodec.textContent = conservative.probe?.encoder || "libx265";
  els.conservativeResolution.textContent = resolution;
  els.conservativeBitrate.textContent = formatBitrate(encodedBitrate(payload, "conservative"));
  els.conservativeStatus.textContent = `${conservative.metric_status || "--"} / ${conservative.status || "--"}`;

  els.splitBadge.textContent = formatPercent(payload.bandwidth_saving_pct);
  setLiveChip(payload.status);

  if (payload.source_playlist_url) {
    attachHls("source", els.sourceVideo, payload.source_playlist_url);
  }
  if (payload.conservative_playlist_url) {
    attachHls("conservative", els.conservativeVideo, payload.conservative_playlist_url);
  }
  els.playBtn.disabled = !(payload.source_playlist_url && payload.conservative_playlist_url);
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollStatus, 1000);
}

async function pollStatus() {
  if (!streamId) return;
  try {
    const payload = await fetchJson(`/api/streams/${streamId}`);
    updateStatus(payload);
    if (!["failed", "stopped"].includes(payload.status)) {
      schedulePoll();
    } else {
      setBusy(false);
    }
  } catch (error) {
    els.errorText.textContent = error.message;
    schedulePoll();
  }
}

async function startStream(rtspUrl) {
  if (streamId) return;
  setBusy(true);
  detachPlayers();
  els.errorText.textContent = "--";
  els.sourceStatus.textContent = "starting";
  els.conservativeStatus.textContent = "starting";
  try {
    const payload = await fetchJson("/api/streams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rtsp_url: rtspUrl }),
    });
    streamId = payload.stream_id;
    updateStatus(payload);
    setBusy(false);
    schedulePoll();
  } catch (error) {
    streamId = null;
    els.errorText.textContent = error.message;
    els.sourceStatus.textContent = "未启动";
    els.conservativeStatus.textContent = "未启动";
    setLiveChip("failed");
    setBusy(false);
  }
}

async function stopStream() {
  if (!streamId) return;
  const stoppingId = streamId;
  streamId = null;
  clearTimeout(pollTimer);
  try {
    const payload = await fetchJson(`/api/streams/${stoppingId}`, {
      method: "DELETE",
    });
    updateStatus(payload);
  } catch (error) {
    els.errorText.textContent = error.message;
    setLiveChip("failed");
  } finally {
    detachPlayers();
    els.rtspInput.disabled = false;
    els.sourceStatus.textContent = "已停止";
    els.conservativeStatus.textContent = "已停止";
    setBusy(false);
  }
}

els.tabs.forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.target));
});
els.streamForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const rtspUrl = els.rtspInput.value.trim();
  if (rtspUrl) startStream(rtspUrl);
});
els.stopBtn.addEventListener("click", stopStream);
els.playBtn.addEventListener("click", () => {
  if (els.sourceVideo.paused || els.conservativeVideo.paused) playBoth();
  else pauseBoth();
});

els.divider.addEventListener("mousedown", (event) => {
  dragging = true;
  els.divider.focus();
  event.preventDefault();
});
window.addEventListener("mousemove", (event) => {
  if (dragging) setSplit(positionFromClientX(event.clientX));
});
window.addEventListener("mouseup", () => {
  dragging = false;
});
els.divider.addEventListener("touchstart", () => {
  dragging = true;
}, { passive: true });
els.divider.addEventListener("touchmove", (event) => {
  if (dragging) setSplit(positionFromClientX(event.touches[0].clientX));
}, { passive: true });
window.addEventListener("touchend", () => {
  dragging = false;
});
els.divider.addEventListener("keydown", (event) => {
  const current = Number.parseFloat(els.divider.style.left) || 50;
  if (event.key === "ArrowLeft") {
    setSplit(current - 2);
    event.preventDefault();
  } else if (event.key === "ArrowRight") {
    setSplit(current + 2);
    event.preventDefault();
  }
});

els.sourceVideo.addEventListener("pause", () => {
  if (!els.conservativeVideo.paused) els.conservativeVideo.pause();
  els.controls.classList.remove("playing");
});
els.conservativeVideo.addEventListener("pause", () => {
  if (!els.sourceVideo.paused) els.sourceVideo.pause();
  els.controls.classList.remove("playing");
});

setSplit(Number.parseFloat(new URLSearchParams(location.search).get("p")) || 50);
showTab(new URLSearchParams(location.search).get("tab") === "specialized" ? "specialized" : "generic");
syncTimer = setInterval(synchronizeVideos, 500);
window.addEventListener("beforeunload", () => clearInterval(syncTimer));
