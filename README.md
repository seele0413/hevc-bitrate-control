# H.265 默认编码与三档综合策略研究工具

当前版本为 **v0.11.0**。程序对同一输入独立生成四个视频：

- x265 原生默认编码；
- 保守综合策略；
- 均衡综合策略；
- 激进综合策略。

三档综合策略分别搜索自己的合格 CRF，不做 CRF 配对，不评选胜出方案。最终只展示实际输出分辨率、平均视频包码率及相对默认 x265 的码率节省百分比；负数表示码率增加，不会被截断。

## 当前三档策略

| 模式 | preset | VMAF / P5 / SSIM | ref / B帧 / lookahead | 最大 / 最小 GOP |
|---|---|---|---|---|
| `conservative` | `medium` | 95 / 93 / 0.990 | 4 / 4 / 30 | 2秒 / 1秒 |
| `balanced` | `medium` | 90 / 88 / 0.980 | 5 / 6 / 60 | 4秒 / 2秒 |
| `aggressive` | `slow` | 83 / 80 / 0.950 | 6 / 8 / 90 | 10秒 / 2秒 |

三档均启用 AQ2、`b-adapt=2`、`b-pyramid=1`、模式化 ROI 保护降噪和静态 ROI QP 偏移。滤镜顺序是“分区降噪合成 → `addroi` → libx265”，不启用 VBV，不缩放分辨率。

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
  --output '.\results\monitor-four-strategies'
```

输出包括：

- `default_x265.mp4`
- `composite_conservative.mp4`
- `composite_balanced.mp4`
- `composite_aggressive.mp4`
- `final_metrics.csv`
- `final_summary.md`
- `research_manifest.json`

三档先用前12秒搜索 CRF，再对完整输入复核；完整片段不合格时只降低当前档 CRF。第二次相同运行会复用参考、短片候选和完整候选缓存。

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

- 选择并上传本机 MP4/MKV 视频。
- 后台创建四路编码任务，HTTP 请求不会一直阻塞。
- 第一版最多同时运行一个编码任务，后续任务排队。
- 显示当前阶段和失败原因。
- 展示 x265 原生默认、保守、均衡、激进四路结果。
- 展示分辨率、平均视频包码率和相对默认方案的节省百分比，负节省值会显示为负数。
- 提供四个正式 H.265 输出文件下载。
- 生成 H.264 浏览器预览，并用重叠式视频对比滑块查看默认方案与所选综合策略。

视频对比滑块的实现方式：

- 底层视频始终完整显示。
- 上层视频通过 `clip-path` 裁剪，只显示左侧一部分。
- 拖动中间分割线会改变上层视频的可见宽度。
- 两个视频同步播放、暂停、进度和倍速。
- 只保留一路声音，另一路静音，并定期校准播放时间偏差。

API：

```text
POST /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/results
GET  /api/jobs/{job_id}/files/{filename}
GET  /api/jobs/{job_id}/previews/{strategy_id}
```

任务目录统一保存到 `work/web_jobs/<job_id>/`。H.264 预览只用于浏览器观看，不参与 H.265 码率和画质结论。

## 历史研究命令

以下命令仍保留用于复核早期 AQ、ROI、降噪、VBV 和等画质研究，但不属于 v0.11.0 的四路正式输出：

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

- 当前是离线参数实验，不是实时 RTSP 产品。
- 当前只验证视频流，音频不参与编码和码率统计。
- 两方案独立搜索、肉眼无损近似配对、视频包字节码率、质量驱动 VBV、AQ2/AQ3/AQ4、固定机位静态 ROI、ROI 保护降噪、激进模式 x265 slow 研究、三种正式结论和可恢复 `compare` 命令已经实现；长期平均码率反馈、动态/语义 ROI 和最终网页仍未完成。
- “不影响画质”在工程上定义为通过设定的客观指标门槛，不等于数学意义上的像素完全无损。
