# H.264 原生编码与 V1.9 H.265 固定参数研究工具

当前版本为 **v1.9.0**。正式入口 `multi-encode` 只生成两路结果：

- `default_h264.mp4`：只指定 `libx264`，保留 FFmpeg/libx264 原生默认参数。
- `hevc_fixed.mp4`：固定 H.265 参数方案，参数为 `CRF 36.0 · preset fast · GOP 2-10s · ref 4 · b-frames 4 · lookahead 45 · 无roi · 无降噪`。

V1.6 不做 CRF 搜索、不做 ROI、不做降噪、不评选胜出方案，也不输出摄像头部署结论。码率节省百分比只作为数据记录；负数必须保留，表示 H.265 固定参数方案相对 H.264 原生编码码率增加。

## 使用方法

在项目根目录打开 PowerShell，先检查环境：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
```

执行 V1.6 两路编码：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab multi-encode `
  --input 'F:\work\课题\监控素材.mp4' `
  --output '.\results\monitor-v1_6'
```

`--roi-config` 仍可传入以兼容旧命令行，但 V1.6 正式流程不会读取 ROI 配置，也不会生成 `addroi` 或 `hqdn3d` 分区降噪。

输出包括：

- `default_h264.mp4`
- `hevc_fixed.mp4`
- `final_metrics.csv`
- `final_summary.md`
- `research_manifest.json`

视频码率按 `v:0` 视频流压缩包字节计算，音频和容器开销不进入核心比较。

## 本地 Web

启动本地网页：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab web --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000/
```

本地首页挂载 `apps/web`，用于单 RTSP 输入的双路实时二次编码预览。后端只持续拉取并解码一次源流，把同一组帧送入 `h264_native`（只指定 `libx264`）与 `h265_optimized`（V1.6 H.265 固定参数）。两路原生编码字节在预览转码前按最近 30 秒统计码率与节省率。

浏览器不直接接收 HEVC。两路原生结果分别解码后，统一使用同一套低延迟 H.264 参数生成 HLS 等价预览，从而避免浏览器 HEVC MSE/HLS 不兼容导致的黑屏。实时链路采用约 5 秒、最多 512 MiB 的连续性优先原始帧缓冲，队列满时等待而不主动丢弃旧帧；HLS 保留 20 个 1 秒分片，两路积累约 5 秒后共同起播，并只用 `0.98-1.02x` 微调同步。预期整体延迟约为 8-10 秒。如果 H.265 长期编码速度低于 `1.0x`，有限缓冲仍无法阻止最终积压。网页关闭时会发送停止信标；后端连续 10 秒收不到页面心跳时自动回收该会话的全部 FFmpeg。实时页不生成固定时长样本报告。

实时预览延迟属于本机二次编码链路，不等同于摄像头端一次编码部署延迟；本页面用于观察黑屏、卡顿、码率和预览延迟风险，不生成摄像头部署结论。

RTSP 地址可能包含账号密码，程序不会在状态接口回显完整地址；不要把真实 RTSP 地址写入 Git、公开文档或报告。

## Cloudflare Pages 静态展示版

纯静态展示入口位于 `apps/demo/`，不上传视频、不编码视频、不调用 FastAPI，也不依赖 Python 或 FFmpeg。它从 `data/results.json` 读取 V1.6 展示数据，蓝色参数标签固定在分割竖线右侧，接近右边界时由父容器自然裁切，不拉伸。

Cloudflare Pages 配置：

```text
Framework preset: None
Build command: echo "no build"
Build output directory: apps/demo
```

## 边界

- V1.6 只验证软件编码工具链下的 H.264 原生与 H.265 固定参数输出，不代表摄像头硬件实机已经验证成功。
- 上传的 MP4/MKV 是压缩后的输入参考视频，不称为传感器原始帧。
- 历史 `compare`、`search-crf`、`roi-study`、`denoise-study` 等研究命令保留用于复核旧实验，但不属于 V1.6 正式入口。
