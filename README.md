# H.265 默认编码与 V1.4 预算中性 ROI 策略研究工具

当前版本为 **v1.4.0**。程序对同一输入独立生成四个结果：

- x265 原生默认编码；
- 通用无 ROI 方案；
- 预算中性 ROI 方案；
- ROI + 降噪实验项。

三个非默认策略分别搜索自己的合格 CRF，不做 CRF 配对，不评选胜出方案。最终展示实际输出分辨率、平均视频包码率、CRF、VMAF/P5/SSIM、编码速度、相对默认 x265 的码率节省百分比、相对通用无 ROI 方案的码率节省百分比、ROI 是否预算中性、ROI 重点区域是否保持/改善；负数表示码率增加，不会被截断。

V1.4 的核心变化是：ROI 不再允许无限增加总码率。程序必须先编码通用无 ROI 方案，得到平均视频包码率预算；ROI 候选只能在该预算内重新分配码率，不能额外增加总码率。ROI 候选还必须保证 critical/evidence 重点区域局部 VMAF、P5、SSIM 不低于通用无 ROI 方案；超过预算或重点区域下降时，直接标记 ROI 失败，不伪造成收益。

## 当前 V1.4 四路策略

| 公开策略 | 内部来源 | preset | 区域处理 | ref / B帧 / lookahead | 最大 / 最小 GOP | CRF 搜索 |
|---|---|---|---|---|---|---:|
| 默认 | x265 原生默认 | 原生默认 | 无 | 原生默认 | 原生默认 | 不搜索 |
| 通用无 ROI | V1.0 原激进结构的通用版 | `medium` | 无 ROI、无降噪 | 6 / 8 / 90 | 10秒 / 2秒 | 18～38 |
| 预算中性 ROI | 通用无 ROI + 静态 ROI QP | `medium` | aggressive ROI QP | 6 / 8 / 90 | 10秒 / 2秒 | 18～38 |
| ROI + 降噪实验项 | 通用无 ROI + 静态 ROI QP + ROI 降噪 | `medium` | aggressive ROI QP + aggressive ROI 降噪 | 6 / 8 / 90 | 10秒 / 2秒 | 18～38 |

三个非默认策略统一启用 AQ2：`aq-mode=2`、`aq-strength=1.0`、`qg-size=32`、`aq-motion=0`，并统一使用 VMAF ≥ 83、P5 ≥ 80、SSIM ≥ 0.950 作为最低画质门槛。ROI + 降噪实验项的滤镜顺序是“分区降噪合成 → `addroi` → libx265”；通用无 ROI 没有区域滤镜。

CRF 搜索使用前 12 秒片段寻找满足 VMAF/P5/SSIM 门槛的最高 CRF；随后用该 CRF 编码完整输入。完整视频复核不通过时，只降低当前档 CRF 继续验证，直到通过或到达 CRF 18。ROI 两路在完整视频复核后追加预算和局部画质门槛；未通过时不发布对应 ROI MP4。

## 使用方法

在项目根目录打开 PowerShell：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
```

说明：GitHub 源码仓库不提交 `.tools/ffmpeg/` 下的 FFmpeg/FFprobe 二进制文件，因为它们超过 GitHub 单文件大小限制。新机器运行时可以把带 `libx265`、`libx264`、`libvmaf`、`addroi` 和 `hqdn3d` 的 FFmpeg 放回 `.tools/ffmpeg/bin/`，也可以安装到系统 `PATH`，或通过 `HEVC_LAB_FFMPEG`、`HEVC_LAB_FFPROBE` 环境变量指定路径。

执行当前正式四路编码：

```powershell
python -m hevc_lab multi-encode `
  --input 'F:\work\课题\监控素材.mp4' `
  --roi-config '.\configs\camera-entrance-roi.json' `
  --output '.\results\monitor-v1_4'
```

输出包括：

- `default_x265.mp4`
- `generic_no_roi.mp4`
- `budget_neutral_roi.mp4`：仅在预算和重点区域门槛通过时生成。
- `roi_denoise_experimental.mp4`：仅在预算和重点区域门槛通过时生成。
- `final_metrics.csv`：四路固定行，包含分辨率、平均视频包码率、CRF、VMAF/P5/SSIM、编码速度、相对默认节省、相对通用节省、预算中性和 ROI 局部结论。
- `final_summary.md`
- `research_manifest.json`

三档先用前12秒搜索 CRF，再对完整输入复核；完整片段不合格时只降低当前档 CRF。第二次相同运行会复用参考、短片候选和完整候选缓存。码率节省百分比只用于记录，不作为胜出或部署结论。

## 当前真实素材 V1.4 回归结果

2026-07-24 使用 `F:\work\课题\监控素材.mp4` 完成 60 秒四路回归，结果位于 `results/monitor-v1_4`：

| 编码策略 | 状态 | CRF | 平均/尝试视频包码率 | VMAF / P5 / SSIM | 编码速度 | 相对默认节省 | 相对通用节省 | 预算中性 |
|---|---|---:|---:|---|---:|---:|---:|:---:|
| x265 原生默认 | 已生成 | — | 0.568393 Mbit/s | — | — | — | — | — |
| 通用无 ROI 方案 | 已生成 | 36.5 | 0.206501 Mbit/s | 85.438 / 80.577 / 0.975043 | 2.071x | 63.67% | 0.00% | — |
| 预算中性 ROI 方案 | 失败 | 37.5 | 0.274063 Mbit/s | 85.612 / 80.406 / 0.973342 | 2.297x | — | -32.72% | 否 |
| ROI + 降噪实验项 | 失败 | 38.0 | 0.255483 Mbit/s | 84.908 / 81.829 / 0.971679 | 1.963x | — | -23.72% | 否 |

ROI 两路全局质量均达到 VMAF/P5/SSIM 门槛，但平均视频包码率超过通用无 ROI 方案的 0.206501 Mbit/s 预算，因此 V1.4 正确标记失败，未生成 `budget_neutral_roi.mp4` 和 `roi_denoise_experimental.mp4`。已生成的 `default_x265.mp4` 与 `generic_no_roi.mp4` 均完成 FFmpeg 全文件解码检查，退出码为0。

## 历史真实素材 V1.1 回归结果

使用 `F:\work\课题\监控素材.mp4` 完成 60 秒四路回归，结果位于 `results/monitor-four-strategies-v1_1`：

| 编码策略 | CRF | 平均视频包码率 | 相对默认节省 | 目标区间命中 |
|---|---:|---:|---:|:---:|
| x265 原生默认 | — | 0.568393 Mbit/s | — | — |
| 保守综合策略 | 30.0 | 0.557291 Mbit/s | 1.95% | 否，低于 10%～15% |
| 均衡综合策略 | 31.0 | 0.530009 Mbit/s | 6.75% | 否，低于 20%～30% |
| 激进综合策略 | 38.0 | 0.294857 Mbit/s | 48.12% | 不适用 |

这表示 V1.1 程序逻辑已能按目标区间选择和如实标记结果，但当前保守/均衡参数在这段素材上还没达到设定节省目标。

## 历史真实素材 V1.2 回归结果

2026-07-24 使用 `F:\work\课题\监控素材.mp4` 完成 60 秒四路回归，结果位于 `results/monitor-aggressive-plus-v1_2`：

| 编码策略 | CRF | 平均视频包码率 | VMAF / P5 / SSIM | 编码速度 | 相对默认节省 |
|---|---:|---:|---|---:|---:|
| x265 原生默认 | — | 0.568393 Mbit/s | — | — | — |
| 激进+综合策略 | 39.0 | 0.237818 Mbit/s | 83.891 / 83.043 / 0.967868 | 0.989x | 58.16% |
| 激进++综合策略 | 38.0 | 0.269104 Mbit/s | 83.810 / 83.265 / 0.967848 | 0.863x | 52.66% |
| 激进+++综合策略 | 37.0 | 0.312427 Mbit/s | 83.516 / 82.978 / 0.967412 | 1.001x | 45.03% |

这次结果说明：三档“参数更激进”不等于最终码率一定更低。激进++和激进+++的更强背景降噪、ROI QP 与更长 GOP 使短片画质边界提前下降，最终必须使用更低 CRF 才能通过 VMAF/P5/SSIM 门槛，所以平均码率反而高于激进+。本报告仍只记录数据，不评选胜出方案，也不生成部署结论。

## 本地 Web 界面

启动本地网页：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab web --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000/
```

也可以在项目根目录双击 `start_web.cmd`。

Web 界面能力：

- 本地入口挂载 `apps/demo_live`，视觉复用 `apps/demo` 的居中卡片、通用/专用 Tab、双视频重叠画面和拖动分割线。
- 输入原生 H.264 RTSP 与 H.265 保守策略 RTSP 两路地址并启动本地实时预览；后端用 FFmpeg 拉流并生成浏览器可播 HLS，浏览器不能直接播放 `rtsp://`。
- 原生 H.264 显示摄像头当前 H.264 编码流；H.265 保守策略显示摄像头侧已按既有保守策略输出的 H.265/HEVC 流。
- 后端会单独统计两路摄像头输入码流的实时视频包码率，用于估算带宽节省；H.264 HLS 预览只用于网页播放，不参与节省率、VMAF、CRF 或部署结论。
- 离线上传编码入口保留在页面底部折叠区，可选择并上传本机 MP4/MKV 视频。
- 后台创建四路编码任务，HTTP 请求不会一直阻塞。
- 第一版最多同时运行一个编码任务，后续任务排队。
- 显示当前阶段和失败原因。
- 展示 x265 原生默认、通用无 ROI、预算中性 ROI、ROI + 降噪实验项四路结果。
- 展示分辨率、平均视频包码率、CRF、VMAF/P5/SSIM、编码速度、相对默认方案、相对通用方案、预算中性和 ROI 重点区域结论，负节省值会显示为负数。
- 提供四个正式 H.265 输出文件下载。
- 离线编码结果在底部折叠区展示指标和下载链接。

实时预览页面的实现方式：

- `通用` Tab 显示原生 H.264 与 H.265 保守策略两路摄像头流的重叠式实时对比。
- `专用` Tab 预留给后续 ROI、降噪或实时编码策略对比。
- 页面只回显脱敏后的 RTSP 地址、两路 codec/分辨率/fps、双 HLS 状态、实时码率、预览延迟和 FFmpeg 错误摘要。
- 离线编码结果在底部折叠区展示指标和下载链接，不作为实时预览的一部分。

API：

```text
POST /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/results
GET  /api/jobs/{job_id}/files/{filename}
GET  /api/jobs/{job_id}/previews/{strategy_id}
```

任务目录统一保存到 `work/web_jobs/<job_id>/`。H.264 预览只用于浏览器观看，不参与 H.265 码率和画质结论。

RTSP 实时预览 API：

```text
POST   /api/streams
GET    /api/streams/{stream_id}
GET    /api/streams/{stream_id}/hls/source/{filename}
GET    /api/streams/{stream_id}/hls/conservative/{filename}
DELETE /api/streams/{stream_id}
```

`POST /api/streams` 正式入参为：

```json
{
  "source_rtsp_url": "rtsp://...",
  "conservative_rtsp_url": "rtsp://..."
}
```

实时预览使用 `work/live_streams/<stream_id>/source/` 和 `work/live_streams/<stream_id>/conservative/` 保存临时 HLS 分片，并使用 `work/live_streams/<stream_id>/metrics/` 保存只用于实时码率估算的临时分片。停止拉流时会结束 FFmpeg 进程并清理临时文件。RTSP 地址可能包含账号密码，程序不会在状态接口中回显完整地址；不要把真实 RTSP 地址写入 Git、公开文档或报告。

## Cloudflare Pages 静态展示版

项目另有一个纯静态展示入口：`apps/demo/`。它和本地 Web 功能版分离，不上传视频、不编码视频、不调用 FastAPI，也不依赖 Python、FFmpeg、任务队列或文件上传 API。

静态版目录结构：

```text
apps/demo/
├─ index.html
├─ styles.css
├─ app.js
├─ data/results.json
└─ videos/
   ├─ default_preview.mp4
   ├─ conservative_preview.mp4
   ├─ default_x265.mp4
   └─ conservative_hevc.mp4
```

当前 demo 展示离线结果；浏览器预览使用 `videos/*_preview.mp4`，H.265/HEVC 正式文件只作为下载链接提供。替换新实验结果时，更新 `apps/demo/data/results.json` 中的 `previewSrc`、`hevcDownload` 和指标字段，并把对应文件放入 `apps/demo/videos/`。

Cloudflare Pages 配置：

```text
Framework preset: None
Build command: echo "no build"
Build output directory: apps/demo
```

Build command 也可以留空。预览 MP4 建议单文件小于 25MiB；当前 `apps/demo/videos/` 下的预览和 H.265 下载文件均低于该限制。

## 历史研究命令

以下命令仍保留用于复核早期 AQ、ROI、降噪、VBV 和等画质研究，但不属于 v1.0.0 的四路正式输出：

对单个方案执行 CRF 18～38、0.5 精度的自适应画质搜索：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab search-crf `
  --input '.\samples\interframe-sample.mkv' `
  --output '.\results\single-search' `
  --mode balanced `
  --scheme optimized `
  --duration 12
```

搜索先测 CRF 18、28、38，再缩小合格边界并复核最终点左右相邻的 0.5 CRF 点。发现明显非单调时自动补测全部 41 点。相同输入和参数再次运行会复用候选视频及 VMAF/SSIM 指标缓存。

单方案搜索输出：

- `search.json`：搜索范围、评估顺序、模式参数、全部测试点和最终点。
- `quality_points.csv`：逐 CRF 的码率、VMAF、P5、SSIM、速度和缓存状态。
- `search_summary.md`：中文边界结论。

独立搜索工程基线与优化组合，并按肉眼无损近似标准配对：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab pair-crf `
  --input '.\samples\interframe-sample.mkv' `
  --output '.\results\pair-search' `
  --mode balanced `
  --duration 12
```

程序先让两路分别通过所选模式的 VMAF、P5 和 SSIM 绝对门槛，再在各自最高合格 CRF 与低一档相邻点中寻找 `|ΔVMAF|≤1.0` 的最接近配对。找不到时返回“证据不足”，不会用远高于门槛的 CRF 18 锚点强行配对。

配对开发报告包括 `pair_search.json`、`pair_quality_points.csv` 和 `pair_search_summary.md`。该命令不生成部署文件；正式流程使用下文的 `compare` 命令。

## 质量驱动码控

质量优先模式使用“自适应 CRF＋VBV 峰值保护”，不是固定设置一个很低的目标码率：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab rate-control `
  --input '.\samples\interframe-sample.mkv' `
  --output '.\results\rate-control' `
  --mode balanced `
  --scheme optimized `
  --duration 12
```

程序先搜索自然 CRF 画质边界，再按所选模式从严格到宽松测试 VBV 上限。候选必须满足：

- 模式的 VMAF、P5、SSIM 绝对门槛。
- 相对无上限 CRF 的 `|ΔVMAF|≤1.0`。
- 平均码率降低且1秒峰值不增加。

画质失败会自动放宽峰值倍率，全部失败则回退无上限 CRF。码率统一来自 `v:0` 视频压缩包字节，音频和容器开销不参与。

## AQ 自适应量化研究

当前 x265 medium 默认已经使用 AQ2。`aq-study` 把默认 AQ2 作为对照，让暗场 AQ3 和边缘 AQ4 分别搜索自己的画质边界，再执行等画质配对：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab aq-study `
  --input 'F:\work\课题\监控素材.mp4' `
  --output '.\results\aq-monitor-12s-balanced' `
  --mode balanced `
  --scheme optimized `
  --duration 12
```

候选必须同时通过模式的 VMAF、P5、SSIM 和 `|ΔVMAF|≤1.0` 门槛；保守/综合还须通过0.97x速度门槛，激进模式不使用速度淘汰。候选只有在等画质下取得正向平均码率收益才采用，否则回退默认 AQ2。保守/综合/激进模式分别使用 0.8/1.0/1.2 AQ 强度，综合和激进模式把 qg-size 缩小到16以提高空间调节粒度。

AQ3、AQ4 是方差、暗场和边缘驱动的块级 QP 调整，不是人物、机器人或文字识别。

## 固定机位静态 ROI

`roi-study` 使用 FFmpeg [`addroi`](https://ffmpeg.org/ffmpeg-filters.html#addroi) 将固定区域的量化偏移交给 libx265。无 ROI AQ2 对照和 ROI 候选会分别搜索画质边界，再同时检查全局和重点区域画质：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab roi-study `
  --input 'F:\work\课题\监控素材.mp4' `
  --roi-config '.\configs\camera-entrance-roi.json' `
  --output '.\results\roi-monitor-12s-balanced' `
  --mode balanced `
  --scheme optimized `
  --duration 12
```

内置入口机位配置只适用于 1920×1080，分辨率不匹配时直接报错，不静默缩放坐标。综合模式保护中央玻璃门、室外主通道、右侧停车/门口和时间戳，并提高左侧静态室内的 QP。无实际平均码率收益时自动回退无 ROI 对照。

输出包含 `roi_study.json`、`roi_quality_points.csv`、`roi_region_quality.csv`、`roi_study_summary.md` 和 `roi_overlay.png`（红=critical，黄=evidence，蓝=discard）。当前只实现固定坐标 ROI，不检测或跟踪人员/车辆；隐私区必须另行遮挡或模糊，不能用高 QP 替代。

## ROI 保护的无效噪声抑制

`denoise-study` 复用固定机位 ROI 配置，对左侧静态室内使用较强 `hqdn3d`，普通背景使用中等强度，三个关键区域只做轻度降噪，时间戳保持原图直通。无降噪 AQ2 对照和降噪候选会各自搜索画质边界，并复算关键区域指标：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab denoise-study `
  --input 'F:\work\课题\监控素材.mp4' `
  --roi-config '.\configs\camera-entrance-roi.json' `
  --output '.\results\denoise-monitor-12s-balanced' `
  --mode balanced `
  --scheme optimized `
  --duration 12
```

只有全局 VMAF/P5/SSIM、模式速度策略、三个 critical 区域、evidence 时间戳和平均视频包码率全部通过时才选中降噪；激进模式的速度策略是不设硬门槛。否则自动回退无降噪 AQ2 对照。输出包含 `denoise_study.json`、`denoise_quality_points.csv`、`denoise_region_quality.csv`、`denoise_study_summary.md` 和 `denoise_overlay.png`。

当前阶段只处理随机空域/时域噪声，不代表已解决雨雪、烟雾、光线闪烁或过度锐化振铃；摄像头增益、曝光和锐化仍应优先在设备侧控制。

## 激进模式 x265 slow 研究

激进模式正式使用 `slow`。`preset-study` 固定帧间参数及其他编码条件，让 `medium` 与 `slow` 分别寻找自己的画质边界，再执行等画质配对：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab preset-study `
  --input 'F:\work\课题\监控素材.mp4' `
  --output '.\results\preset-monitor-aggressive' `
  --mode aggressive `
  --scheme optimized `
  --duration 12
```

输出包含 `preset_study.json`、`preset_quality_points.csv`、`preset_pair.csv` 和 `preset_study_summary.md`。速度仍会记录为 `offline`、`near_realtime`、`realtime` 或 `realtime_headroom`，但激进模式不会因为低于0.97x而拒绝slow。

## 三种独立正式结论

`pair-crf` 在完成等画质配对后会给出三个互不替代的结论：

- **算法可行性**：优化组合相对工程基线取得严格正收益，并达到所选模式的算法节省门槛。
- **软件画面连续性**：所有模式都检查完整解码、分辨率、帧率、帧数、时长和逐帧时间戳。保守/综合还要求速度≥0.97x；激进不设速度硬门槛，低于1.0x时以“离线连续”通过。`≥1.0x`标记当前电脑可实时处理，`≥1.1x`额外标记为具有工程余量。
- **部署可行性初筛**：优化组合相对输入源流达到所选模式的源流节省门槛时，只标记“值得进入摄像头实机验证”；否则保持原码流直通。

三个结论都会写入 `pair_search.json` 的 `conclusions` 字段和 `pair_search_summary.md`，不会再把算法收益直接表述为部署成功。

## 正式 compare 命令与断点恢复

`compare` 是当前统一入口，串联参考准备、双路搜索、等画质配对、画面连续性验证和三种正式结论：

```powershell
& "C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe" -m hevc_lab compare `
  --input "F:\work\课题\监控素材.mp4" `
  --output ".\results\monitor-compare" `
  --mode balanced `
  --duration 60
```

程序持续更新 `comparison_state.json`，记录请求哈希、阶段、尝试次数和失败原因。中断后执行完全相同的命令会复用FFV1参考、候选视频和VMAF/SSIM指标；已完整完成且候选文件仍存在时直接读取 `comparison.json`。输入内容、模式、片段范围、质量/节省门槛或程序版本变化都会生成新的实验身份，不会误用旧的整次实验结论。

`--min-saving` 可同时覆盖算法与源流节省门槛；如果需要独立设置，使用 `--min-algorithm-saving` 和 `--min-source-saving`，独立参数优先。

输出目录包含：

- `selected_*.mp4`：仅在优化候选同时满足所选模式全部部署门槛时生成
- `comparison.csv`：各候选完整对比
- `experiment.json`：机器可读实验记录
- `summary.md`：中文结论
- `work/candidates`：候选视频
- `work/logs`：编码和指标日志

## 当前边界

- 当前正式研究主体仍是离线参数实验；本地 RTSP 只用于实时画面预览和双画面对比，不生成实时节码率或部署结论。
- 当前只验证视频流，音频不参与编码和码率统计。
- 两方案独立搜索、肉眼无损近似配对、视频包字节码率、质量驱动 VBV、AQ2/AQ3/AQ4、固定机位静态 ROI、ROI 保护降噪、激进模式 x265 slow 研究、三种正式结论、可恢复 `compare` 命令、本地 Web 上传与四路预览已经实现；长期平均码率反馈、动态/语义 ROI 和摄像头实机控制仍未完成。
- “不影响画质”在工程上定义为通过设定的客观指标门槛，不等于数学意义上的像素完全无损。
