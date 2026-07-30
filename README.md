# V2.1.0 H.264 源码直流与 H.265 实时编码工具

这是一个本机实时 Web 工具，只接受 H.264 RTSP 视频。

- 左路：原始 H.264 elementary stream，按字节统计后以 `-c:v copy` 重封装为 HLS。
- 右路：同一源流解码后，以固定参数实时编码 H.265；H.265 字节先统计，再转换为 H.264 HLS 供浏览器观看。
- 码率：两路都按最近 30 秒 elementary stream 字节计算，HLS 容器和右路观看预览不计入。
- 起播：两路都建立约 3 秒共同缓冲后才显示画面，前 2 秒不做同步调速；右路仅观看预览使用 `libx264 ultrafast / CRF 21`。
- 性能：页面以约 30 秒编码积压趋势显示“实时稳定”或“积压增长”，单次 `0.99x` 只作为处理节奏参考。

## 环境检查

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
```

环境只需要 FFmpeg、FFprobe、H.264 解码、`libx265`、用于浏览器预览的 `libx264`、RTSP 输入和 HLS 输出。

## 启动网页

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab web --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`，输入 H.264 RTSP 地址。服务固定只监听本机地址，同时只运行一个实时会话。

也可以双击 `start_web.cmd` 启动。

## 固定 H.265 参数

```text
CRF 36.0 · preset fast · Main 8-bit · yuv420p
ref 4 · bframes 4 · b-adapt 2 · lookahead 45
GOP 10 秒 · min GOP 2 秒 · scenecut 40
cutree · weightp · AQ2
```

唯一配置源是 `hevc_lab/config.py`。

## 实时接口

- `GET /api/health`
- `GET /api/runtime`
- `POST /api/streams`，请求体只允许 `{"rtsp_url": "rtsp://..."}`
- `GET /api/streams/{stream_id}`
- `DELETE /api/streams/{stream_id}`
- `POST /api/streams/{stream_id}/heartbeat`
- `POST /api/streams/{stream_id}/stop`
- `GET /api/streams/{stream_id}/hls/{variant}/{filename}`

variant 固定为 `source` 与 `h265_optimized`。状态响应提供两路播放列表、两路滚动码率与字节数、H.265 编码速度和积压、队列状态及 `bandwidth_saving_pct`。负百分比表示码率增加。

RTSP 地址在日志和状态中会删除凭据、完整路径、query 与 fragment。不要把真实地址写入代码、文档或测试。

## 验证

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m compileall -q hevc_lab tests
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest discover -v
node --check apps\web\app.js
```

`work/` 中已有用户结果不会被自动删除，但当前程序不会读取这些旧结果。
