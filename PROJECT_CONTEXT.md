# H.265 课题共享上下文

## 项目位置

- 当前实体目录：`F:\work\project_hevc-bitrate-control\h265-mvp`
- 当前 Codex 工作目录：`C:\Users\31969\.codex\worktrees\f00d\hbc`
- Python 3.9：`C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe`
- 项目内工具：`.tools\ffmpeg\bin\ffmpeg.exe`、`.tools\ffmpeg\bin\ffprobe.exe` 和 `.tools\vmaf`。

## 当前课题目标

当前唯一正式方向是 **V1.6 H.264 原生编码与 H.265 固定参数方案研究工具**。

正式入口 `multi-encode` 对同一个输入参考视频生成两路结果：

1. `default_h264.mp4`：只指定 `libx264`，保留 FFmpeg/libx264 原生默认编码参数。
2. `hevc_fixed.mp4`：H.265 固定参数方案，参数为 `CRF 36.0 · preset medium · GOP 2-10s · ref 4 · b-frames 4 · lookahead 45 · 无roi · 无降噪`。

V1.6 不做 CRF 搜索、不使用 ROI、不使用分区降噪、不评选胜出方案、不生成部署结论。码率节省百分比只作为记录；负数表示码率增加，必须保留。

## 当前代码状态

- `hevc_lab/__init__.py` 与 `pyproject.toml` 版本为 `1.7.0`。
- `hevc_lab/core/configs.py` 的正式 `multi_encode_strategies()` 只返回 `hevc_fixed` 一个 H.265 固定参数方案；`multi_encode_modes()` 返回 `h264_native` 与 `hevc_fixed`。
- `hevc_lab/multi_encode.py` 的 `MULTI_ENCODE_PIPELINE_VERSION` 为 `v1.7.0`，正式流程先生成 H.264 原生编码，再生成 H.265 固定参数方案。
- `hevc_lab/encoders/x265.py` 同时保留 `encode_default_x265()` 历史函数和 `encode_default_h264()` V1.6 函数。
- `hevc_lab/reports/multi_writer.py` 输出 V1.6 两路摘要，不评选最佳方案，不输出部署结论。
- 本地 Web 首页挂载 `apps/web`；静态 Cloudflare Pages 展示版位于 `apps/demo`。

## Web 与预览边界

本地 RTSP 实时预览只建立一个持续 RTSP 输入连接，解码后把同一组 `yuv420p` 帧分发给 H.264 原生参数编码与 H.265 固定参数优化编码。两路原生编码字节在预览转码前按最近 30 秒统计码率；随后分别解码，并统一用 `libx264 ultrafast / CRF 18 / zerolatency / 1 秒 GOP` 生成浏览器 HLS 等价预览。因此浏览器不直接接收 HEVC，不依赖 HEVC MSE/HLS 支持。实时页不生成固定时长样本报告；预览画面和码率证据必须明确区分。

实时链路采用连续性优先的约 5 秒共享帧缓冲，容量同时受 512 MiB 原始帧内存上限约束；队列满时对上游施加背压，不再主动删除旧帧。HLS 保留 20 个 1 秒分片，前端两路都积累约 5 秒后才共同起播，以 `0.98-1.02x` 微调同步，仅在偏差超过 3 秒时回到两路共同可播放的较旧时间点。该方案用约 8-10 秒整体延迟换取画面连续性；若 H.265 长期编码速度低于 `1.0x`，任何有限缓冲仍会最终耗尽。页面每 2 秒发送心跳，关闭页面会发送停止信标；后端连续 10 秒收不到页面心跳时终止整组 FFmpeg。实时预览延迟属于本机二次编码与等价预览链路，不等同于摄像头端一次编码部署延迟。

`apps/demo` 是纯静态展示版，不上传视频、不编码视频、不调用 FastAPI。蓝色 H.265 参数标签固定在分割竖线右侧，接近右边界时由父容器自然裁切，不拉伸。

## 当前关键决策

1. 当前正式入口只发布 `default_h264.mp4` 和 `hevc_fixed.mp4`。
2. H.265 固定参数必须保持 `CRF 36.0 · preset medium · GOP 2-10s · ref 4 · b-frames 4 · lookahead 45 · 无roi · 无降噪`。
3. V1.6 不读取 ROI 配置；`--roi-config` 仅为旧命令兼容保留。
4. 平均码率只按 `v:0` 视频流包字节计算，音频和容器开销不进入核心比较。
5. 软件编码实验不能表述为摄像头硬件实机验证成功。
6. 上传的 MP4/MKV 是压缩后的输入参考视频，不称为传感器原始帧。

## 常用命令

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest discover -v
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab multi-encode --input 'F:\work\课题\监控素材.mp4' --output '.\results\monitor-v1_6'
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab web --host 127.0.0.1 --port 8000
```

## 历史说明

V1.4 预算中性 ROI、V1.3 四路策略、V1.2 激进三档、`compare`、`roi-study`、`denoise-study` 等能力保留为历史研究内容。它们不属于 V1.6 当前正式入口，后续新任务不得把旧 ROI 四路输出恢复为当前方案，除非用户明确要求另开历史分支。
