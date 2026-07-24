const els = {
  pageTitle: document.querySelector("#pageTitle"),
  pageSubtitle: document.querySelector("#pageSubtitle"),
  genericTabTitle: document.querySelector("#genericTabTitle"),
  genericTabDesc: document.querySelector("#genericTabDesc"),
  specializedTabTitle: document.querySelector("#specializedTabTitle"),
  specializedTabDesc: document.querySelector("#specializedTabDesc"),
  tabs: document.querySelectorAll(".strategy-tab"),
  viewerGeneric: document.querySelector("#viewerGeneric"),
  viewerSpecialized: document.querySelector("#viewerSpecialized"),
  stage: document.querySelector("#stage"),
  divider: document.querySelector("#divider"),
  baseVideo: document.querySelector("#baseVideo"),
  oursVideo: document.querySelector("#oursVideo"),
  baseName: document.querySelector("#baseName"),
  baseResolution: document.querySelector("#baseResolution"),
  baseBitrate: document.querySelector("#baseBitrate"),
  baseCrf: document.querySelector("#baseCrf"),
  oursName: document.querySelector("#oursName"),
  oursResolution: document.querySelector("#oursResolution"),
  oursBitrate: document.querySelector("#oursBitrate"),
  oursCrf: document.querySelector("#oursCrf"),
  savingBadge: document.querySelector("#savingBadge"),
  controls: document.querySelector("#controls"),
  playBtn: document.querySelector("#playBtn"),
  progress: document.querySelector("#progress"),
  timeLabel: document.querySelector("#timeLabel"),
  rateSelect: document.querySelector("#rateSelect"),
  metricsText: document.querySelector("#metricsText"),
  downloadBtn: document.querySelector("#downloadBtn"),
  downloadMenu: document.querySelector("#downloadMenu"),
  placeholderTitle: document.querySelector("#placeholderTitle"),
  placeholderDesc: document.querySelector("#placeholderDesc"),
  loadError: document.querySelector("#loadError"),
};

let data = null;
let dragging = false;

function fmtTime(t) {
  if (!Number.isFinite(t)) return "0:00";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtBitrate(v) {
  return Number.isFinite(v) ? `${v.toFixed(3)} Mbit/s` : "--";
}

function fmtCrf(v) {
  return Number.isFinite(v) ? v.toFixed(1) : "--";
}

function setSplit(pct) {
  const next = Math.max(0, Math.min(100, pct));
  els.oursVideo.style.clipPath = `inset(0 0 0 ${next}%)`;
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

function syncSlave(force = false) {
  const drift = Math.abs((els.oursVideo.currentTime || 0) - (els.baseVideo.currentTime || 0));
  if (force || drift > 0.15) {
    els.oursVideo.currentTime = els.baseVideo.currentTime || 0;
  }
  els.oursVideo.playbackRate = els.baseVideo.playbackRate;
}

function playBoth() {
  syncSlave(true);
  els.oursVideo.play().catch(() => {});
  els.baseVideo.play().catch(() => {});
  els.controls.classList.add("playing");
}

function pauseBoth() {
  els.baseVideo.pause();
  els.oursVideo.pause();
  els.controls.classList.remove("playing");
}

function updateProgress() {
  syncSlave(false);
  const duration = els.baseVideo.duration || 0;
  const current = els.baseVideo.currentTime || 0;
  if (duration > 0) {
    const ratio = current / duration;
    const pct = (ratio * 100).toFixed(1);
    els.progress.value = Math.round(ratio * 1000);
    els.progress.style.background =
      `linear-gradient(to right, var(--blue) 0%, var(--blue) ${pct}%, var(--line) ${pct}%, var(--line) 100%)`;
  }
  els.timeLabel.textContent = `${fmtTime(current)} / ${fmtTime(duration)}`;
}

function renderDownloads() {
  const links = [
    { href: data.baseline.hevcDownload, label: "下载默认 H.265" },
    { href: data.ours.hevcDownload, label: "下载保守 H.265" },
    { href: data.metricsDownload, label: "下载指标 CSV" },
  ];
  els.downloadMenu.innerHTML = links
    .map((link) => `<a href="${link.href}" download>${link.label}</a>`)
    .join("");
}

function render() {
  els.pageTitle.textContent = data.page.title;
  els.pageSubtitle.textContent = data.page.subtitle;
  els.genericTabTitle.textContent = data.ours.tabTitle;
  els.genericTabDesc.textContent = data.ours.params;
  els.specializedTabTitle.textContent = data.specialized.tabTitle;
  els.specializedTabDesc.textContent = data.specialized.title;
  els.placeholderTitle.textContent = data.specialized.title;
  els.placeholderDesc.textContent = data.specialized.description;

  els.baseVideo.src = data.baseline.previewSrc;
  els.oursVideo.src = data.ours.previewSrc;
  els.baseVideo.muted = false;
  els.oursVideo.muted = true;

  els.baseName.textContent = data.baseline.displayName;
  els.baseResolution.textContent = data.baseline.resolution;
  els.baseBitrate.textContent = fmtBitrate(data.baseline.bitrateMbps);
  els.baseCrf.textContent = fmtCrf(data.baseline.crf);

  els.oursName.textContent = data.ours.displayName;
  els.oursResolution.textContent = data.ours.resolution;
  els.oursBitrate.textContent = fmtBitrate(data.ours.bitrateMbps);
  els.oursCrf.textContent = fmtCrf(data.ours.crf);
  els.savingBadge.textContent = `${data.ours.savingVsDefaultPct.toFixed(2)}%`;
  els.metricsText.textContent =
    `VMAF ${data.ours.vmaf.toFixed(3)} · P5 ${data.ours.vmafP5.toFixed(3)} · ` +
    `SSIM ${data.ours.ssim.toFixed(4)} · 编码速度 ${data.ours.encodeSpeedX.toFixed(3)}x`;
  renderDownloads();
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
els.playBtn.addEventListener("click", () => {
  if (els.baseVideo.paused) playBoth();
  else pauseBoth();
});
els.progress.addEventListener("input", () => {
  const duration = els.baseVideo.duration || 0;
  const next = (Number(els.progress.value) / 1000) * duration;
  els.baseVideo.currentTime = next;
  els.oursVideo.currentTime = next;
  updateProgress();
});
els.rateSelect.addEventListener("change", () => {
  const rate = Number(els.rateSelect.value || 1);
  els.baseVideo.playbackRate = rate;
  els.oursVideo.playbackRate = rate;
});
els.baseVideo.addEventListener("timeupdate", updateProgress);
els.baseVideo.addEventListener("loadedmetadata", updateProgress);
els.baseVideo.addEventListener("pause", () => {
  if (els.oursVideo.paused) return;
  pauseBoth();
});
els.baseVideo.addEventListener("ended", pauseBoth);
els.downloadBtn.addEventListener("click", () => {
  els.downloadMenu.hidden = !els.downloadMenu.hidden;
});
document.addEventListener("click", (e) => {
  if (!els.downloadMenu.contains(e.target) && !els.downloadBtn.contains(e.target)) {
    els.downloadMenu.hidden = true;
  }
});

async function init() {
  setSplit(Number.parseFloat(new URLSearchParams(location.search).get("p")) || 50);
  try {
    const response = await fetch("./data/results.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`results.json 加载失败：${response.status}`);
    data = await response.json();
    render();
    const tab = new URLSearchParams(location.search).get("tab");
    showTab(tab === "specialized" ? "specialized" : "generic");
  } catch (error) {
    els.loadError.hidden = false;
    els.loadError.textContent = error.message;
  }
}

init();
