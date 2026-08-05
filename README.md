# V2.2.2 HEVC 直接播放实时工具

这是面向远程监控的本机 Web 工具，只接受 H.264 RTSP 视频。

- 左路：H.264 elementary stream 按字节统计后，以 `stream copy` 重封装为 MPEG-TS HLS。
- 右路：同一源流解码后通过阻塞队列进入固定参数 `libx265`，输出带时间戳 MPEG-TS，再以 `hvc1`、`-c:v copy` 重封装为 HEVC fMP4 HLS。
- 右路文件：`init.mp4` 与 `segment_*.m4s`。分片由正式 H.265 GOP 关键帧边界决定，实际时长可能接近 10 秒。
- H.265 elementary 码率由 MPEG-TS PES 载荷统计；TS/PES 容器开销、HLS 初始化文件和媒体分片开销不进入正式节省率。
- HLS 传输诊断单独统计最新约 30 秒已封口媒体分片，源码使用 `.ts`，右路使用 `.m4s`。
- 浏览器使用 `hls.js 1.6.16`。页面提交 RTSP 前必须检测 HEVC MSE 或原生 HEVC HLS 能力，不支持时不会启动会话。

## 环境检查

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
```

项目使用 `.tools\ffmpeg\bin\ffmpeg.exe` 和 `.tools\ffmpeg\bin\ffprobe.exe`。环境需要 H.264 解码、`libx265`、MPEG-TS 输入/输出、RTSP 输入和 HLS 输出；不需要 `libx264` 预览编码器。

## 启动网页

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab web --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`，输入 H.264 RTSP 地址。也可以运行 `start_web.cmd`。

## 固定 H.265 参数

```text
CRF 36.0 · preset fast · Main 8-bit · yuv420p
ref 4 · bframes 4 · b-adapt 2 · lookahead 45
GOP 10 秒 · min GOP 2 秒 · scenecut 40
cutree · weightp · AQ2 · qg-size 32 · aq-motion 0
```

直接播放 HLS 参数由 `hevc_lab/config.py` 唯一提供：`hvc1`、fMP4、`init.mp4`、10 秒目标分片和 `.m4s`。

## 实时接口

- `GET /api/health`
- `GET /api/runtime`
- `POST /api/streams`，请求体只允许 `{"rtsp_url": "rtsp://..."}`
- `GET /api/streams/{stream_id}`
- `DELETE /api/streams/{stream_id}`
- `POST /api/streams/{stream_id}/heartbeat`
- `POST /api/streams/{stream_id}/stop`
- `GET /api/streams/{stream_id}/hls/{variant}/{filename}`

variant 固定为 `source` 和 `h265_optimized`。状态接口包含 elementary stream 最近 30 秒码率、HLS 传输诊断、编码速度、积压、队列和 `bandwidth_saving_pct`。负节省率表示码率增加。

## 验证命令

```powershell
node --check apps\web\app.js
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m compileall -q hevc_lab tests
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest discover -v
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
```

120 秒长测：

```powershell
$env:HBC_LONG_INTEGRATION='1'
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest -v tests.test_integration.RealFfmpegPipelineTests.test_remote_stable_1080p_pipeline_runs_for_120_seconds
```

本副本不自动提交或推送 GitHub，也不读取或删除历史 `work/` 结果。
