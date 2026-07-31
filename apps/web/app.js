const els = {
  streamForm: document.querySelector("#streamForm"),
  rtspInput: document.querySelector("#rtspInput"),
  startBtn: document.querySelector("#startBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  stage: document.querySelector("#stage"),
  divider: document.querySelector("#divider"),
  savingLabel: document.querySelector("#savingLabel"),
  savingBadge: document.querySelector("#savingBadge"),
  emptyState: document.querySelector("#emptyState"),
  startupMessage: document.querySelector("#startupMessage"),
  sourceVideo: document.querySelector("#sourceVideo"),
  h265Video: document.querySelector("#h265Video"),
  sourceResolution: document.querySelector("#sourceResolution"),
  h265Resolution: document.querySelector("#h265Resolution"),
  sourceBitrate: document.querySelector("#sourceBitrate"),
  h265Bitrate: document.querySelector("#h265Bitrate"),
  sourceBytes: document.querySelector("#sourceBytes"),
  sourceLatency: document.querySelector("#sourceLatency"),
  h265Latency: document.querySelector("#h265Latency"),
  h265Backlog: document.querySelector("#h265Backlog"),
  controls: document.querySelector("#controls"),
  playBtn: document.querySelector("#playBtn"),
  liveChip: document.querySelector("#liveChip"),
  timeLabel: document.querySelector("#timeLabel"),
  encodeState: document.querySelector("#encodeState"),
  encodeSpeed: document.querySelector("#encodeSpeed"),
  streamStatus: document.querySelector("#streamStatus"),
  maskedUrl: document.querySelector("#maskedUrl"),
  outputStatus: document.querySelector("#outputStatus"),
  bufferStatus: document.querySelector("#bufferStatus"),
  backlogTrend: document.querySelector("#backlogTrend"),
  warningText: document.querySelector("#warningText"),
  errorText: document.querySelector("#errorText"),
  configSummary: document.querySelector("#configSummary"),
  runtimeError: document.querySelector("#runtimeError"),
};

const players = { source: null, h265_optimized: null };
const playerEntries = [
  { key: "source", video: els.sourceVideo, label: "源码路" },
  { key: "h265_optimized", video: els.h265Video, label: "H.265 路" },
];
const playerStates = {
  source: { ready: false, recovering: false, recoveryTargetTime: null, relocated: false },
  h265_optimized: { ready: false, recovering: false, recoveryTargetTime: null, relocated: false },
};
const PLAYBACK_TARGET_DELAY_SECONDS = 0;
const PLAYBACK_RECOVERY_BUFFER_SECONDS = 1;
const H265_PREVIEW_HLS_SEGMENT_SECONDS = 0.5;
const PLAYBACK_DELAY_TOLERANCE_SECONDS = 1;
const PLAYBACK_STALL_GUARD_SECONDS = 1;
const SYNC_GRACE_MS = 2000;
const SLOW_PLAYBACK_RATE = 0.98;
const FAST_PLAYBACK_RATE = 1.02;
const HLS_RETENTION_SECONDS = 60;
const BACKLOG_TREND_WINDOW_MS = 30000;
const BACKLOG_TREND_MIN_SPAN_MS = 10000;
const BACKLOG_GROWTH_THRESHOLD_SECONDS_PER_MINUTE = 0.5;

let dragging = false;
let streamId = null;
let pollTimer = null;
let latencyTimer = null;
let heartbeatTimer = null;
let sourcePlaylistUrl = null;
let h265PlaylistUrl = null;
let playbackReady = false;
let playbackStarted = false;
let playbackStartedAt = null;
let userPaused = false;
let stopping = false;
let splitFrame = null;
let pendingSplit = 50;
let currentSplit = 50;
let backlogSamples = [];
let rtspReferenceElapsed = null;
let rtspReferenceSampledAt = null;

function setSplit(pct) {
  const next = Math.max(0, Math.min(100, pct));
  pendingSplit = next;
  if (splitFrame) return;
  splitFrame = window.requestAnimationFrame(() => {
    currentSplit = pendingSplit;
    els.stage.style.setProperty("--p", currentSplit);
    els.stage.style.setProperty("--split", `${currentSplit}%`);
    els.divider.setAttribute("aria-valuenow", Math.round(currentSplit));
    splitFrame = null;
  });
}

function posFromEvent(clientX) {
  const rect = els.stage.getBoundingClientRect();
  return ((clientX - rect.left) / rect.width) * 100;
}

function setBusy(busy) {
  const active = Boolean(streamId);
  els.startBtn.disabled = busy || active || stopping;
  els.rtspInput.disabled = busy || active || stopping;
  els.stopBtn.disabled = !active || stopping;
}

function setLiveChip(status) {
  els.liveChip.classList.remove("running", "failed", "starting", "stopping");
  const labels = {
    running: "实时处理中",
    failed: "处理失败",
    starting: "启动中",
    stopping: "停止中",
    stopped: "已停止",
    idle: "未连接",
  };
  els.liveChip.textContent = labels[status] || "未连接";
  if (status === "running") els.liveChip.classList.add("running");
  if (status === "failed") els.liveChip.classList.add("failed");
  if (status === "starting") els.liveChip.classList.add("starting");
  if (status === "stopping") els.liveChip.classList.add("stopping");
}

function metricNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatBitrate(value) {
  const number = metricNumber(value);
  return Number.isFinite(number) ? `${number.toFixed(2)} Mbps` : "--";
}

function formatLatency(value) {
  const number = metricNumber(value);
  return Number.isFinite(number) && number >= 0 ? `${number.toFixed(1)} s` : "--";
}

function formatBytes(value) {
  const number = metricNumber(value);
  if (!Number.isFinite(number)) return "--";
  if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(1)} MiB`;
  return `${Math.round(number / 1024)} KiB`;
}

function setStartupMessage(message) {
  if (els.startupMessage) els.startupMessage.textContent = message;
}

function updatePlaybackLabel() {
  const recoveringCount = playerEntries.filter(({ key }) => playerStates[key].recovering).length;
  if (recoveringCount > 0) {
    els.timeLabel.textContent =
      `${recoveringCount} 路缓冲恢复中 · 安全缓冲 ${PLAYBACK_RECOVERY_BUFFER_SECONDS}s`;
  } else if (userPaused) {
    els.timeLabel.textContent =
      `双路已暂停 · 无人为目标延迟`;
  } else {
    els.timeLabel.textContent =
      `各路贴近最新安全边缘播放 · 安全缓冲 ${PLAYBACK_RECOVERY_BUFFER_SECONDS}s`;
  }
}

function updateRtspReference(payload) {
  const elapsed = metricNumber(payload.rtsp_realtime_elapsed_seconds);
  if (!Number.isFinite(elapsed)) {
    rtspReferenceElapsed = null;
    rtspReferenceSampledAt = null;
    return;
  }
  rtspReferenceElapsed = elapsed;
  rtspReferenceSampledAt = performance.now();
}

function rtspRealtimeSeconds() {
  if (!Number.isFinite(rtspReferenceElapsed) || !Number.isFinite(rtspReferenceSampledAt)) {
    return null;
  }
  return rtspReferenceElapsed + Math.max(0, performance.now() - rtspReferenceSampledAt) / 1000;
}

function resetBacklogTrend() {
  backlogSamples = [];
  if (els.encodeState) els.encodeState.textContent = "采样中";
  if (els.encodeSpeed) els.encodeSpeed.textContent = "处理节奏 --";
  if (els.backlogTrend) els.backlogTrend.textContent = "等待数据";
  const health = els.encodeState?.closest(".speed-readout");
  if (health) health.classList.remove("stable", "growing");
}

function sampleBacklog(value) {
  const backlog = metricNumber(value);
  if (!Number.isFinite(backlog) || backlog < 0) return null;
  const now = performance.now();
  const previous = backlogSamples[backlogSamples.length - 1];
  if (!previous || now - previous.time >= 800) {
    backlogSamples.push({ time: now, backlog });
  }
  backlogSamples = backlogSamples.filter(
    (sample) => now - sample.time <= BACKLOG_TREND_WINDOW_MS,
  );
  if (backlogSamples.length < 2) return { state: "sampling", spanMs: 0 };

  const first = backlogSamples[0];
  const last = backlogSamples[backlogSamples.length - 1];
  const spanMs = last.time - first.time;
  if (spanMs < BACKLOG_TREND_MIN_SPAN_MS) return { state: "sampling", spanMs };

  const points = backlogSamples.map((sample) => ({
    x: (sample.time - first.time) / 1000,
    y: sample.backlog,
  }));
  const meanX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  const meanY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
  const covariance = points.reduce(
    (sum, point) => sum + (point.x - meanX) * (point.y - meanY),
    0,
  );
  const variance = points.reduce(
    (sum, point) => sum + (point.x - meanX) ** 2,
    0,
  );
  const ratePerMinute = variance > 0 ? (covariance / variance) * 60 : 0;
  const deltas = backlogSamples.slice(1).map(
    (sample, index) => sample.backlog - backlogSamples[index].backlog,
  );
  const nonDecreasingRatio = deltas.filter((delta) => delta >= -0.03).length / deltas.length;
  const netGrowth = last.backlog - first.backlog;
  const requiredGrowth = Math.max(
    0.08,
    BACKLOG_GROWTH_THRESHOLD_SECONDS_PER_MINUTE * (spanMs / 60000),
  );
  const growing = ratePerMinute > BACKLOG_GROWTH_THRESHOLD_SECONDS_PER_MINUTE &&
    netGrowth >= requiredGrowth &&
    nonDecreasingRatio >= 0.7;
  return { state: growing ? "growing" : "stable", ratePerMinute, spanMs };
}

function updateEncodeHealth(backlog, speed) {
  const trend = sampleBacklog(backlog);
  const speedValue = metricNumber(speed);
  els.encodeSpeed.textContent = Number.isFinite(speedValue)
    ? `处理节奏 ${speedValue.toFixed(2)}x`
    : "处理节奏 --";

  const health = els.encodeState?.closest(".speed-readout");
  if (health) health.classList.remove("stable", "growing");
  if (!trend) {
    els.encodeState.textContent = "采样中";
    els.backlogTrend.textContent = "等待数据";
    return;
  }
  if (trend.state === "sampling") {
    els.encodeState.textContent = "采样中";
    els.backlogTrend.textContent = `已采样 ${Math.floor(trend.spanMs / 1000)} 秒`;
    return;
  }

  const rate = Math.abs(trend.ratePerMinute) < 0.05 ? 0 : trend.ratePerMinute;
  const prefix = rate > 0 ? "+" : "";
  els.backlogTrend.textContent = `约 ${prefix}${rate.toFixed(1)} s/min`;
  if (trend.state === "growing") {
    els.encodeState.textContent = "积压增长";
    if (health) health.classList.add("growing");
  } else {
    els.encodeState.textContent = "实时稳定";
    if (health) health.classList.add("stable");
  }
}

function resolutionFromProbe(probe) {
  return probe && probe.width && probe.height ? `${probe.width}x${probe.height}` : "--";
}

function output(payload, variant) {
  return payload.outputs?.[variant] || {};
}

function metrics(payload, variant) {
  return output(payload, variant).metrics || {};
}

function outputError(payload) {
  for (const variant of ["source", "h265_optimized"]) {
    if (output(payload, variant).error) return output(payload, variant).error;
  }
  return payload.error || "--";
}

function outputStatus(payload) {
  const source = output(payload, "source").status || "--";
  const h265 = output(payload, "h265_optimized").status || "--";
  return `源码 ${source} / H.265 ${h265}`;
}

function configValue(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "--";
  return `${value}${suffix}`;
}

function configDecimal(value) {
  const numeric = metricNumber(value);
  return Number.isFinite(numeric) ? numeric.toFixed(1) : "--";
}

function renderConfig(config, denoise = {}, playback = {}) {
  if (!config || typeof config !== "object" || !els.configSummary) return;
  const crf = metricNumber(config.crf);
  const profile = String(config.profile || "main").toLowerCase() === "main"
    ? "Main 8-bit"
    : config.profile;
  const cards = [
    ["码率控制", `CRF ${Number.isFinite(crf) ? crf.toFixed(1) : "--"}`],
    ["速度档位", `preset ${configValue(config.preset)}`],
    ["输出格式", `${configValue(profile)} · ${configValue(config.pixel_format)}`],
    [
      "帧间结构",
      `ref ${configValue(config.ref)} · B ${configValue(config.bframes)} · adapt ${configValue(config.b_adapt)}`,
    ],
    [
      "前瞻与 AQ",
      `lookahead ${configValue(config.lookahead)} · AQ${configValue(config.aq_mode)} · qg ${configValue(config.qg_size)}`,
    ],
    [
      "GOP 与工具",
      `${configValue(config.min_gop_seconds)}-${configValue(config.gop_seconds)} 秒 · scenecut ${configValue(config.scenecut)} · cutree/weightp`,
    ],
    [
      "噪声抑制",
      `${configValue(denoise.filter)} · ${denoise.profile === "light_detail_preserving" ? "轻度保细节" : configValue(denoise.profile)}`,
    ],
    [
      "降噪强度",
      `空间 Y/C ${configDecimal(denoise.luma_spatial)}/${configDecimal(denoise.chroma_spatial)} · 时间 Y/C ${configDecimal(denoise.luma_temporal)}/${configDecimal(denoise.chroma_temporal)}`,
    ],
    [
      "播放安全边缘",
      `目标延迟 ${configValue(playback.target_delay_seconds, "s")} · 安全缓冲 ${configValue(playback.recovery_buffer_seconds, "s")}`,
    ],
    [
      "右路预览 HLS",
      `目标分片 ${configValue(playback.h265_preview_hls_segment_seconds, "s")} · 保留 ${configValue(playback.hls_playlist_segments)} 片`,
    ],
  ];
  els.configSummary.replaceChildren(
    ...cards.map(([label, value]) => {
      const card = document.createElement("div");
      const labelNode = document.createElement("span");
      const valueNode = document.createElement("strong");
      card.className = "param-card";
      labelNode.textContent = label;
      valueNode.textContent = value;
      card.append(labelNode, valueNode);
      return card;
    }),
  );
}

function playerRtspLatency(video) {
  const rtspSeconds = rtspRealtimeSeconds();
  if (!Number.isFinite(rtspSeconds) || !Number.isFinite(video.currentTime)) return null;
  return Math.max(0, rtspSeconds - video.currentTime);
}

function updateLatencyLabels() {
  els.sourceLatency.textContent = formatLatency(playerRtspLatency(els.sourceVideo));
  els.h265Latency.textContent = formatLatency(playerRtspLatency(els.h265Video));
}

function playerTimeline(video) {
  if (!video.seekable || video.seekable.length === 0) return null;
  const index = video.seekable.length - 1;
  const start = video.seekable.start(index);
  const end = video.seekable.end(index);
  return Number.isFinite(start) && Number.isFinite(end) ? { start, end } : null;
}

function delayTargetFor(video) {
  const timeline = playerTimeline(video);
  const rtspSeconds = rtspRealtimeSeconds();
  if (!timeline || !Number.isFinite(rtspSeconds)) return null;
  const latestSafe = timeline.end - PLAYBACK_RECOVERY_BUFFER_SECONDS;
  if (!Number.isFinite(latestSafe) || latestSafe < timeline.start) return null;
  const desired = rtspSeconds - PLAYBACK_TARGET_DELAY_SECONDS;
  const target = Math.max(timeline.start, Math.min(desired, latestSafe));
  return target;
}

function seekPlayer(video, target) {
  video.currentTime = target;
  video.playbackRate = 1;
}

function bufferedAheadAt(video, target) {
  if (!video.buffered) return 0;
  for (let index = 0; index < video.buffered.length; index += 1) {
    const start = video.buffered.start(index);
    const end = video.buffered.end(index);
    if (target >= start - 0.05 && target <= end + 0.05) {
      return Math.max(0, end - target);
    }
  }
  return 0;
}

function enterPlayerRecovery(key, message) {
  if (!playbackReady || userPaused || dragging || stopping) return;
  const entry = playerEntries.find((item) => item.key === key);
  if (!entry) return;
  const state = playerStates[key];
  if (!state.recovering) {
    state.recoveryTargetTime = entry.video.currentTime;
    state.relocated = false;
  }
  state.recovering = true;
  entry.video.pause();
  entry.video.playbackRate = 1;
  setStartupMessage(message);
  updatePlaybackLabel();
}

function resumePlayerWhenReady(key) {
  const entry = playerEntries.find((item) => item.key === key);
  if (!entry) return false;
  const state = playerStates[key];
  if (!state.recovering) return false;
  const video = entry.video;
  const timeline = playerTimeline(video);
  if (!timeline) return false;
  let target = state.recoveryTargetTime;
  const inWindow = Number.isFinite(target) &&
    target >= timeline.start &&
    target <= timeline.end - PLAYBACK_RECOVERY_BUFFER_SECONDS;
  if (!inWindow) {
    target = delayTargetFor(video);
    state.recoveryTargetTime = target;
    state.relocated = true;
  }
  if (!Number.isFinite(target)) return false;
  target = Math.max(timeline.start, Math.min(target, timeline.end - PLAYBACK_RECOVERY_BUFFER_SECONDS));
  if (timeline.end - target < PLAYBACK_RECOVERY_BUFFER_SECONDS) return false;
  if (Math.abs(video.currentTime - target) > 0.1) seekPlayer(video, target);
  if (video.readyState < 3 || bufferedAheadAt(video, target) < PLAYBACK_RECOVERY_BUFFER_SECONDS) {
    return false;
  }
  state.recovering = false;
  state.recoveryTargetTime = null;
  setStartupMessage(state.relocated
    ? `${entry.label} 已回到 RTSP 实时基准安全位置`
    : `${entry.label} 缓冲恢复，继续播放`);
  state.relocated = false;
  updatePlaybackLabel();
  if (!userPaused) video.play().catch(() => {});
  return true;
}

function maybeStartBufferedPlayback() {
  if (playbackReady) return true;
  const targets = new Map();
  for (const { key, video } of playerEntries) {
    const target = delayTargetFor(video);
    if (!Number.isFinite(target)) return false;
    targets.set(key, target);
  }
  for (const { key, video } of playerEntries) {
    seekPlayer(video, targets.get(key));
    playerStates[key].ready = true;
    playerStates[key].recovering = false;
    playerStates[key].recoveryTargetTime = null;
    playerStates[key].relocated = false;
  }
  playbackReady = true;
  setStartupMessage(`RTSP 实时基准播放准备完成`);
  els.stage.classList.add("active");
  els.emptyState.classList.add("hide");
  els.playBtn.disabled = false;
  if (!playbackStarted) playBoth(true);
  return true;
}

function maintainPlayerDelay(key) {
  const entry = playerEntries.find((item) => item.key === key);
  if (!entry) return;
  const video = entry.video;
  if (video.paused) return;
  const timeline = playerTimeline(video);
  if (!timeline) return;
  if (video.currentTime < timeline.start || video.currentTime > timeline.end) {
    enterPlayerRecovery(key, `${entry.label} 播放窗口已变化，正在恢复`);
    return;
  }
  if (timeline.end - video.currentTime < PLAYBACK_STALL_GUARD_SECONDS) {
    enterPlayerRecovery(key, `${entry.label} 缓冲不足，暂停等待分片`);
    return;
  }
  const target = delayTargetFor(video);
  if (!Number.isFinite(target)) return;
  const delta = target - video.currentTime;
  if (delta > PLAYBACK_DELAY_TOLERANCE_SECONDS) {
    video.playbackRate = FAST_PLAYBACK_RATE;
  } else if (delta < -PLAYBACK_DELAY_TOLERANCE_SECONDS) {
    video.playbackRate = SLOW_PLAYBACK_RATE;
  } else {
    video.playbackRate = 1;
  }
}

function controlPlayers() {
  if (dragging) return;
  if (!playbackReady && !maybeStartBufferedPlayback()) return;
  if (playbackStartedAt !== null && performance.now() - playbackStartedAt < SYNC_GRACE_MS) {
    for (const { video } of playerEntries) video.playbackRate = 1;
    return;
  }
  for (const { key } of playerEntries) {
    if (playerStates[key].recovering) {
      resumePlayerWhenReady(key);
    } else {
      maintainPlayerDelay(key);
    }
  }
}

function updateSaving(payload) {
  const saving = metricNumber(payload.bandwidth_saving_pct);
  els.savingBadge.classList.remove("increase");
  if (!Number.isFinite(saving)) {
    els.savingLabel.textContent = "方案差异";
    els.savingBadge.textContent = "--";
  } else if (saving < 0) {
    els.savingLabel.textContent = "方案增加";
    els.savingBadge.textContent = `${Math.abs(saving).toFixed(1)}%`;
    els.savingBadge.classList.add("increase");
  } else {
    els.savingLabel.textContent = "方案节省";
    els.savingBadge.textContent = `${saving.toFixed(1)}%`;
  }
}

function attachHls(video, url, key) {
  if (!url) return;
  if (players[key]) players[key].destroy();
  players[key] = null;
  if (window.Hls && window.Hls.isSupported()) {
    const hlsConfig = {
      lowLatencyMode: false,
      maxLiveSyncPlaybackRate: 1,
      maxBufferLength: HLS_RETENTION_SECONDS,
      maxMaxBufferLength: HLS_RETENTION_SECONDS,
      backBufferLength: HLS_RETENTION_SECONDS,
    };
    if (PLAYBACK_TARGET_DELAY_SECONDS > 0) {
      hlsConfig.liveSyncDuration = PLAYBACK_TARGET_DELAY_SECONDS;
      hlsConfig.liveMaxLatencyDuration = HLS_RETENTION_SECONDS - PLAYBACK_RECOVERY_BUFFER_SECONDS;
    }
    const player = new window.Hls(hlsConfig);
    player.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data || !data.fatal) return;
      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        enterPlayerRecovery(key, "远程 HLS 分片暂时中断，该路暂停等待恢复");
        player.startLoad();
      } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        enterPlayerRecovery(key, "播放器正在恢复媒体缓冲，该路暂停等待恢复");
        player.recoverMediaError();
      } else {
        player.destroy();
        players[key] = null;
      }
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
    if (players[key]) players[key].destroy();
    players[key] = null;
  });
  sourcePlaylistUrl = null;
  h265PlaylistUrl = null;
  playbackReady = false;
  playbackStarted = false;
  playbackStartedAt = null;
  for (const state of Object.values(playerStates)) {
    state.ready = false;
    state.recovering = false;
    state.recoveryTargetTime = null;
    state.relocated = false;
  }
  userPaused = false;
  rtspReferenceElapsed = null;
  rtspReferenceSampledAt = null;
  for (const video of [els.sourceVideo, els.h265Video]) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  els.emptyState.classList.remove("hide");
  els.stage.classList.remove("active");
  els.controls.classList.remove("playing");
  els.playBtn.disabled = true;
  setStartupMessage("等待 H.264 RTSP");
  resetBacklogTrend();
}

function playBoth(auto = false) {
  if (!playbackReady) return;
  if (!auto) userPaused = false;
  if (!playbackStarted) playbackStartedAt = performance.now();
  playbackStarted = true;
  els.sourceVideo.play().catch(() => {});
  els.h265Video.play().catch(() => {});
  els.controls.classList.add("playing");
  updatePlaybackLabel();
}

function pauseBoth(manual = true) {
  if (manual) userPaused = true;
  els.sourceVideo.pause();
  els.h265Video.pause();
  els.controls.classList.remove("playing");
  for (const video of [els.sourceVideo, els.h265Video]) video.playbackRate = 1;
  updatePlaybackLabel();
}

function updateStatus(payload) {
  const sourceProbe = output(payload, "source").probe || payload.probes?.source || {};
  const h265Probe = output(payload, "h265_optimized").probe || payload.probes?.h265_optimized || {};
  const sourceMetrics = metrics(payload, "source");
  const h265Metrics = metrics(payload, "h265_optimized");
  const buffer = payload.frame_buffer || {};
  updateRtspReference(payload);

  els.streamStatus.textContent = payload.status || "unknown";
  els.maskedUrl.textContent = payload.masked_url || "--";
  els.outputStatus.textContent = outputStatus(payload);
  els.errorText.textContent = outputError(payload);
  els.warningText.textContent = (payload.warnings || []).join("；") || "--";
  els.sourceResolution.textContent = resolutionFromProbe(sourceProbe);
  els.h265Resolution.textContent = resolutionFromProbe(h265Probe);
  els.sourceBitrate.textContent = formatBitrate(sourceMetrics.elementary_bitrate_mbps);
  els.h265Bitrate.textContent = formatBitrate(h265Metrics.elementary_bitrate_mbps);
  els.sourceBytes.textContent = formatBytes(sourceMetrics.elementary_bytes_in_window);
  els.h265Backlog.textContent = formatLatency(h265Metrics.encoder_backlog_seconds);
  updateEncodeHealth(h265Metrics.encoder_backlog_seconds, h265Metrics.encode_speed_x);
  const depth = metricNumber(buffer.depth_frames);
  const capacity = metricNumber(buffer.capacity_frames);
  els.bufferStatus.textContent = Number.isFinite(depth) && Number.isFinite(capacity)
    ? `${depth} / ${capacity} 帧 · 阻塞背压`
    : "--";
  updatePlaybackLabel();
  setLiveChip(payload.status);
  updateLatencyLabels();
  updateSaving(payload);

  if (payload.source_playlist_url && payload.source_playlist_url !== sourcePlaylistUrl) {
    attachHls(els.sourceVideo, payload.source_playlist_url, "source");
    sourcePlaylistUrl = payload.source_playlist_url;
  }
  if (payload.h265_optimized_playlist_url && payload.h265_optimized_playlist_url !== h265PlaylistUrl) {
    attachHls(els.h265Video, payload.h265_optimized_playlist_url, "h265_optimized");
    h265PlaylistUrl = payload.h265_optimized_playlist_url;
  }
  if (!playbackReady) {
    els.emptyState.classList.remove("hide");
    els.stage.classList.remove("active");
    if (sourcePlaylistUrl && h265PlaylistUrl) {
      setStartupMessage("正在建立双路缓冲");
    } else if (sourcePlaylistUrl) {
      setStartupMessage("等待 H.265 预览");
    } else if (h265PlaylistUrl) {
      setStartupMessage("等待源码关键帧");
    } else {
      setStartupMessage("正在连接 H.264 RTSP");
    }
  }
  if (sourcePlaylistUrl && h265PlaylistUrl) maybeStartBufferedPlayback();
  if (payload.status === "failed" || payload.status === "stopped") {
    stopPolling();
    streamId = null;
    stopping = false;
    detachPlayers();
    setBusy(false);
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.detail || `请求失败：${response.status}`);
    error.status = response.status;
    throw error;
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
    controlPlayers();
    updateLatencyLabels();
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
  if (pollTimer) window.clearInterval(pollTimer);
  if (latencyTimer) window.clearInterval(latencyTimer);
  if (heartbeatTimer) window.clearInterval(heartbeatTimer);
  pollTimer = null;
  latencyTimer = null;
  heartbeatTimer = null;
}

async function checkRuntime() {
  try {
    const runtime = await fetchJson("/api/runtime", { cache: "no-store" });
    const variants = runtime.live_preview?.variants || [];
    const playback = runtime.live_preview?.playback || {};
    if (
      runtime.app_version !== "2.3.0" ||
      runtime.pipeline_version !== "v2.3.0" ||
      variants.join(",") !== "source,h265_optimized" ||
      playback.policy !== "independent_rtsp_realtime_delay" ||
      playback.reference !== "rtsp_wall_clock_elapsed_since_first_source_byte" ||
      playback.target_delay_seconds !== PLAYBACK_TARGET_DELAY_SECONDS ||
      playback.recovery_buffer_seconds !== PLAYBACK_RECOVERY_BUFFER_SECONDS ||
      playback.h265_preview_hls_segment_seconds !== H265_PREVIEW_HLS_SEGMENT_SECONDS ||
      playback.hls_retention_seconds !== HLS_RETENTION_SECONDS
    ) {
      throw new Error("当前后端不是 V2.3.0 固定轻度降噪实时入口");
    }
    renderConfig(
      runtime.live_preview?.h265_config,
      runtime.live_preview?.denoise_config,
      playback,
    );
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
  setStartupMessage("正在连接 H.264 RTSP");
  setBusy(true);
  setLiveChip("starting");
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
  if (!streamId || stopping) return;
  const id = streamId;
  stopping = true;
  stopPolling();
  streamId = null;
  setLiveChip("stopping");
  els.streamStatus.textContent = "stopping";
  els.outputStatus.textContent = "正在停止并回收实时进程";
  detachPlayers();
  setBusy(true);
  try {
    updateStatus(await fetchJson(`/api/streams/${id}`, { method: "DELETE" }));
  } catch (error) {
    if (error.status === 404) {
      els.streamStatus.textContent = "stopped";
      els.outputStatus.textContent = "后端会话已结束";
      setLiveChip("stopped");
    } else {
      els.errorText.textContent = error.message;
      setLiveChip("failed");
    }
  } finally {
    stopping = false;
    setBusy(false);
    els.rtspInput.disabled = false;
  }
}

function beginSplitDrag(event) {
  if (dragging) return;
  dragging = true;
  els.stage.classList.add("dragging");
  els.divider.setPointerCapture(event.pointerId);
  setSplit(posFromEvent(event.clientX));
  event.preventDefault();
}

function moveSplitDrag(event) {
  if (!dragging) return;
  setSplit(posFromEvent(event.clientX));
  event.preventDefault();
}

function endSplitDrag(event) {
  if (!dragging) return;
  dragging = false;
  els.stage.classList.remove("dragging");
  if (els.divider.hasPointerCapture(event.pointerId)) {
    els.divider.releasePointerCapture(event.pointerId);
  }
}

els.divider.addEventListener("pointerdown", beginSplitDrag);
els.divider.addEventListener("pointermove", moveSplitDrag);
els.divider.addEventListener("pointerup", endSplitDrag);
els.divider.addEventListener("pointercancel", endSplitDrag);
els.divider.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    setSplit(currentSplit + (event.key === "ArrowLeft" ? -2 : 2));
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
  if (els.sourceVideo.paused && els.h265Video.paused) {
    playBoth(false);
  } else {
    pauseBoth(true);
  }
});
for (const { key, video } of playerEntries) {
  video.addEventListener("pause", () => {
    if (playerEntries.every((entry) => entry.video.paused)) {
      els.controls.classList.remove("playing");
    }
  });
  video.addEventListener("play", () => els.controls.classList.add("playing"));
  video.addEventListener("waiting", () => {
    enterPlayerRecovery(key, "远程分片暂时未连续，该路暂停等待缓冲");
  });
  video.addEventListener("stalled", () => {
    enterPlayerRecovery(key, "远程分片下载延迟，该路暂停等待缓冲");
  });
}
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
