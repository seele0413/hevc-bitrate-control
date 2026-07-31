# V2.3.0 H.264 源码直流与轻度降噪 H.265 实时编码工具

这是一个本机实时 Web 工具，只接受 H.264 RTSP 视频。

- 左路：原始 H.264 elementary stream，按字节统计后以 `-c:v copy` 重封装为 HLS。
- 右路：同一源流解码后执行固定轻度 `hqdn3d`，再以固定参数实时编码 H.265；H.265 字节先统计，再转换为 H.264 HLS 供浏览器观看。
- 码率：两路都按最近 30 秒 elementary stream 字节计算，HLS 容器和右路观看预览不计入；节省结果属于“轻度降噪 + H.265”完整方案，不是纯编码格式对比。
- 起播：后端以第一批源码字节建立 RTSP 实时基准，程序不设人为目标延迟，两路各自贴近自身 HLS 最新安全播放边缘；安全缓冲为 1 秒。
- 连续性：任一路远程 HLS 卡顿时仅该路自动暂停恢复；手动播放/暂停和停止仍同时控制两路，拖动分割线不会暂停视频；HLS 保留 60 个分片，右路仅观看预览使用 `libx264 ultrafast / CRF 21 / 0.5 秒 GOP / 0.5 秒 HLS 目标分片`。
- 性能：页面以约 30 秒编码积压趋势显示“实时稳定”或“积压增长”，单次 `0.99x` 只作为处理节奏参考。

## 环境检查

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
```

环境需要 FFmpeg、FFprobe、H.264 解码、`hqdn3d`、`libx265`、用于浏览器预览的 `libx264`、RTSP 输入和 HLS 输出。

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

固定轻度噪声抑制同样只从该模块读取：

```text
hqdn3d · light_detail_preserving
空间强度 Y/C 1.5/1.0 · 时间强度 Y/C 2.5/2.0
位置：H.264 解码后、H.265 帧队列前
```

## 实时接口

- `GET /api/health`
- `GET /api/runtime`
- `POST /api/streams`，请求体只允许 `{"rtsp_url": "rtsp://..."}`
- `GET /api/streams/{stream_id}`
- `DELETE /api/streams/{stream_id}`
- `POST /api/streams/{stream_id}/heartbeat`
- `POST /api/streams/{stream_id}/stop`
- `GET /api/streams/{stream_id}/hls/{variant}/{filename}`

variant 固定为 `source` 与 `h265_optimized`。runtime 和流状态提供 `denoise_config`；状态响应还提供两路播放列表、RTSP 实时基准秒数、两路滚动码率与字节数、H.265 编码速度和积压、队列状态及 `bandwidth_saving_pct`。负百分比表示完整方案码率增加。

RTSP 地址在日志和状态中会删除凭据、完整路径、query 与 fragment。不要把真实地址写入代码、文档或测试。

## 验证

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m compileall -q hevc_lab tests
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest discover -v
node --check apps\web\app.js
```

`work/` 中已有用户结果不会被自动删除，但当前程序不会读取这些旧结果。
