# V2.3.0 实时源码直流与轻度降噪设计

> 当前实现版本：v2.3.0。本文全文是当前唯一有效方案。

## 1. 输入与输出

系统只接受一个 H.264 RTSP 视频地址。探测到其他视频编码时返回 HTTP 400，不转换、不降级；音频不进入处理链路。

固定输出为：

| variant | 实际处理 | 浏览器画面 | 码率证据 |
|---|---|---|---|
| `source` | H.264 elementary stream 原样重封装 | H.264 HLS 直通 | 分发前的 H.264 字节 |
| `h265_optimized` | 固定轻度 `hqdn3d` 后以固定参数 `libx265` 编码 | 后续 `libx264` 仅观看预览 | 预览转换前的降噪 H.265 字节 |

## 2. 数据流

```text
H.264 RTSP
  -> 单一 FFmpeg 连接，copy 为 H.264 elementary stream
  -> Python 统计一次源字节
     -> 原字节写入 HLS 重封装进程，-c:v copy
     -> 原字节写入 H.264 解码进程
        -> 固定 hqdn3d=1.5:1.0:2.5:2.0 轻度降噪，输出 yuv420p
        -> 约 5 秒 / 最大 512 MiB 阻塞队列
        -> 固定参数 libx265
        -> Python 统计 H.265 字节
        -> libx264 仅观看 HLS 预览
```

分发和编码写入必须处理管道短写。任何写入、读取或子进程退出都会设置会话停止事件并统一终止五个 FFmpeg 进程。

## 3. 唯一处理参数

配置只定义在 `hevc_lab/config.py`：CRF 36.0、preset fast、Main、`yuv420p`、ref 4、bframes 4、b-adapt 2、lookahead 45、GOP 10 秒、min GOP 2 秒、scenecut 40、cutree、weightp 和固定 AQ2。帧单位的 keyint 根据探测帧率计算。

同一模块还固定定义轻度保细节降噪：`hqdn3d`、亮度/色度空间强度 1.5/1.0、亮度/色度时间强度 2.5/2.0，位置固定在 H.264 解码后与 H.265 帧队列前。实时命令、状态与网页必须读取这些唯一配置对象。

## 4. 码率与状态

两路码率都按 elementary stream 字节计算，窗口固定为最近 30 秒。源码在分发到两个消费者之前只计数一次；预览编码字节、音频和 HLS 容器开销均不计入。

```text
bandwidth_saving_pct = (source_bitrate - h265_bitrate) / source_bitrate * 100
```

负值原样返回，网页显示为“方案增加”。`saving_basis` 固定为 `source_h264_elementary_stream_bytes_vs_denoised_h265_elementary_stream_bytes_rolling_30s`，结果表示源码 H.264 与“轻度降噪 + H.265”整套方案差异，不作为纯 H.264/H.265 编码格式结论。状态同时提供两路窗口码率、窗口字节数、H.265 编码速度、编码积压、队列深度和固定降噪配置。

## 5. HLS 与生命周期

- 源码 HLS 使用摄像头原关键帧切片；不以重新编码修正长 GOP。
- 8 秒仍未生成源码播放列表时返回启动/延迟告警。
- 两路 HLS 保留 60 个分片；源码 HLS 仍依赖摄像头关键帧切片，H.265 预览使用 `libx264 ultrafast / CRF 21 / zerolatency / 0.5 秒 GOP / 0.5 秒 HLS 目标分片`，只影响浏览器观看资源，不改变正式 H.265 参数或码率统计。
- HLS 播放列表使用 `no-store/no-cache` 并关闭代理缓冲；唯一命名的 TS 分片只允许浏览器私有短期缓存。
- 后端在收到第一批 H.264 elementary stream 字节时建立 RTSP 实时基准，状态接口返回 `rtsp_realtime_elapsed_seconds`。该值是后端接收基准的 wall-clock 估算，不是摄像头绝对时间戳。
- 页面只有在两路都具备播放窗口后才揭开画面；程序不再设置人为目标延迟，每一路独立贴近自身 HLS 最新安全播放边缘，并夹到该路自己的 `[seekable.start, seekable.end - 1 秒]` 安全窗口。
- 起播后前 2 秒固定使用 `1.0x`；之后每一路按自身最新安全目标点用 `0.98-1.02x` 贴近实时播放边缘。源码路和 H.265 预览路不再为了显示同一帧而互相追赶或共同 seek。
- 任一路出现 `waiting`、`stalled` 或 HLS 恢复错误时，仅该路自动暂停并等待至少 1 秒实际缓冲后恢复；手动播放/暂停、停止和页面关闭仍同时作用于两路。拖动分割线只更新裁剪位置，视频保持连续播放；拖动期间暂缓自动 seek 与调速，松开后恢复自动控制。
- 页面展示“RTSP 滞后”与 H.265 编码积压，二者分别表达。
- 同时只允许一个活动会话；页面心跳、状态查询和 HLS 文件请求共同续租，租约超时为 45 秒。
- 页面停止、租约超时或任一子进程退出时回收所有进程和管道。

## 6. 安全与接口

`POST /api/streams` 的 JSON 只允许一个 `rtsp_url` 字段。状态、错误和日志只保留脱敏地址，不得包含用户名、密码、完整路径、query 或 fragment。HLS 文件路由只接受固定 variant 下的 `.m3u8` 与 `.ts` 单层文件名，并验证解析后路径仍位于会话目录。

CLI 只提供：

```text
python -m hevc_lab check-env
python -m hevc_lab web --host 127.0.0.1 --port 8000
```

运行时与流状态中的播放策略固定为 `independent_rtsp_realtime_delay`，播放参考固定为 `rtsp_wall_clock_elapsed_since_first_source_byte`。环境检查必须验证 `hqdn3d`、H.264 解码、libx265、libx264、RTSP 与 HLS 能力。
