# V2.2.1 Remote Stable 实时编码工具

这是面向远程监控预览的本机 Web 工具，只接受 H.264 RTSP 视频。

- 左路：H.264 elementary stream 按字节统计后，以 `-c:v copy` 重封装为 HLS。
- 右路：同一源流解码后以固定参数编码 H.265；H.265 字节先统计，再生成约 3 Mbps 的 H.264 HLS 供浏览器观看。
- 比较：两路按最近 30 秒 elementary stream 字节计算，HLS 容器和右路观看预览不计入节省率。
- 播放：源码路独立维持约 10 秒 HLS 边缘延迟，H.265 预览路独立维持约 15 秒，不强制同帧。
- 恢复：某一路实际缓冲低于 1.5 秒时只暂停该路，在当前固定延迟目标重新获得至少 8 秒缓冲后恢复。
- 传输：页面分别显示 HLS 传输码率、最近 10 个分片加权下载速度、实际缓冲、带宽余量、卡顿次数和恢复状态。
- 稳定性：HLS 文件以不可变字节快照响应；停止时先终止子进程再关闭管道，避免远程慢传输和背压导致长度竞态或停止阻塞。

## 环境检查

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
```

环境需要 FFmpeg、FFprobe、H.264 解码、`libx265`、用于浏览器预览的 `libx264`、RTSP 输入和 HLS 输出。

## 启动网页

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab web --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`，输入 H.264 RTSP 地址。也可以双击 `start_web.cmd`。

若需要通过 Cloudflare Tunnel 等方式远程访问，应用仍可监听 `127.0.0.1`，由同机隧道转发到该地址；不要直接将未加保护的服务暴露到公网。

## 编码配置

正式 H.265 参数保持 V2.2 不变：

```text
CRF 36.0 · preset fast · Main 8-bit · yuv420p
ref 4 · bframes 4 · b-adapt 2 · lookahead 45
GOP 10 秒 · min GOP 2 秒 · scenecut 40
cutree · weightp · AQ2
```

右路浏览器预览：

```text
libx264 · preset ultrafast · CRF 26
maxrate 3M · bufsize 6M · 保持源分辨率
1 秒 GOP · 1 秒 HLS · 仅供观看
```

两项配置的唯一来源均为 `hevc_lab/config.py`。

## 实时接口

- `GET /api/health`
- `GET /api/runtime`
- `POST /api/streams`，请求体只允许 `{"rtsp_url": "rtsp://..."}`
- `GET /api/streams/{stream_id}`
- `DELETE /api/streams/{stream_id}`
- `POST /api/streams/{stream_id}/heartbeat`
- `POST /api/streams/{stream_id}/stop`
- `GET /api/streams/{stream_id}/hls/{variant}/{filename}`

variant 固定为 `source` 与 `h265_optimized`。状态响应包含 elementary stream 滚动码率与字节、HLS 最近约 30 秒传输指标、H.265 编码速度与积压、队列状态及 `bandwidth_saving_pct`。负百分比表示码率增加。

RTSP 地址在日志和状态中会删除凭据、完整路径、query 与 fragment。不要把真实地址写入代码、文档或测试。

## 验证

```powershell
node --check apps\web\app.js
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m compileall -q hevc_lab tests
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest discover -v
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
$env:HBC_LONG_INTEGRATION='1'
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest -v tests.test_integration.RealFfmpegPipelineTests.test_remote_stable_1080p_pipeline_runs_for_120_seconds
```

该仓库是独立实验副本，不包含原仓库历史 `work/` 数据。第一阶段不处理实际帧率时间基准或共享背压架构问题。
