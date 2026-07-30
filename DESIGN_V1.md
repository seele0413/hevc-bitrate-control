# V2.1.0 实时源码直流设计

> 当前实现版本：v2.1.0。本文全文是当前唯一有效方案。

## 1. 输入与输出

系统只接受一个 H.264 RTSP 视频地址。探测到其他视频编码时返回 HTTP 400，不转换、不降级；音频不进入处理链路。

固定输出为：

| variant | 实际处理 | 浏览器画面 | 码率证据 |
|---|---|---|---|
| `source` | H.264 elementary stream 原样重封装 | H.264 HLS 直通 | 分发前的 H.264 字节 |
| `h265_optimized` | 固定参数 `libx265` 编码 | 后续 `libx264` 仅观看预览 | 预览转换前的 H.265 字节 |

## 2. 数据流

```text
H.264 RTSP
  -> 单一 FFmpeg 连接，copy 为 H.264 elementary stream
  -> Python 统计一次源字节
     -> 原字节写入 HLS 重封装进程，-c:v copy
     -> 原字节写入 H.264 解码进程，输出 yuv420p
        -> 约 5 秒 / 最大 512 MiB 阻塞队列
        -> 固定参数 libx265
        -> Python 统计 H.265 字节
        -> libx264 仅观看 HLS 预览
```

分发和编码写入必须处理管道短写。任何写入、读取或子进程退出都会设置会话停止事件并统一终止五个 FFmpeg 进程。

## 3. H.265 唯一参数

配置只定义在 `hevc_lab/config.py`：CRF 36.0、preset fast、Main、`yuv420p`、ref 4、bframes 4、b-adapt 2、lookahead 45、GOP 10 秒、min GOP 2 秒、scenecut 40、cutree、weightp 和固定 AQ2。帧单位的 keyint 根据探测帧率计算。

实时命令、流状态的 `fixed_config` 和 `/api/runtime` 的 `h265_config` 必须读取同一个配置对象。

## 4. 码率与状态

两路码率都按 elementary stream 字节计算，窗口固定为最近 30 秒。源码在分发到两个消费者之前只计数一次；预览编码字节、音频和 HLS 容器开销均不计入。

```text
bandwidth_saving_pct = (source_bitrate - h265_bitrate) / source_bitrate * 100
```

负值原样返回，网页显示为“码率增加”。状态同时提供两路窗口码率、窗口字节数、H.265 编码速度、编码积压、队列深度和固定 `saving_basis`。

## 5. HLS 与生命周期

- 源码 HLS 使用摄像头原关键帧切片；不以重新编码修正长 GOP。
- 8 秒仍未生成源码播放列表时返回启动/延迟告警。
- 两路 HLS 保留 20 个分片；H.265 预览使用 `libx264 ultrafast / CRF 18 / zerolatency / 1 秒 GOP`。
- 页面等待两路约 5 秒共同缓冲后播放，以 `0.98-1.02x` 调速同步，偏差超过 3 秒才重新定位。
- 浏览器播放延迟与 H.265 编码积压分别展示。
- 同时只允许一个活动会话；页面心跳租约为 10 秒。
- 页面停止、租约超时或任一子进程退出时回收所有进程和管道。

## 6. 安全与接口

`POST /api/streams` 的 JSON 只允许一个 `rtsp_url` 字段。状态、错误和日志只保留脱敏地址，不得包含用户名、密码、完整路径、query 或 fragment。HLS 文件路由只接受固定 variant 下的 `.m3u8` 与 `.ts` 单层文件名，并验证解析后路径仍位于会话目录。

CLI 只提供：

```text
python -m hevc_lab check-env
python -m hevc_lab web --host 127.0.0.1 --port 8000
```
