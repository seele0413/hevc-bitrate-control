# V2.2.2 HEVC 直接播放实时设计

本文只描述 V2.2.2 当前有效方案。

## 1. 输入与五进程管线

系统只接受一个 H.264 RTSP 视频地址，音频不进入处理链路。启动时用 FFprobe 确认视频编码为 H.264，并获得分辨率与帧率。

```text
RTSP 输入
  -> H.264 elementary stream
     -> 源码 HLS 重封装（stream copy，MPEG-TS）
     -> H.264 解码（yuv420p）
        -> 约 5 秒、最大 512 MiB 的有界阻塞队列
           -> 唯一 libx265 固定参数编码
              -> 带时间戳 MPEG-TS
                 -> H.265 fMP4 HLS 重封装（stream copy）
```

FFmpeg 进程固定为 RTSP 输入、源码 HLS、H.264 解码、H.265 编码和 H.265 HLS 重封装五个。任一进程、管道读写或关键线程失败，都会设置停止事件并统一 terminate/wait/kill 全组进程。

## 2. 正式 H.265 编码

唯一配置位于 `hevc_lab/config.py`：

```text
CRF 36.0 · preset fast · Main 8-bit · yuv420p
ref 4 · bframes 4 · b-adapt 2 · lookahead 45
GOP 10 秒 · min GOP 2 秒 · scenecut 40
cutree 1 · weightp 1 · AQ mode 2 · strength 1.0
qg-size 32 · aq-motion 0
```

输出格式为带时间戳 MPEG-TS。正式 GOP 仍允许 2 到 10 秒，HLS 不重新编码修正关键帧，因此右路分片由关键帧边界决定，实际时长可以接近 10 秒。

## 3. H.265 直接播放 HLS

H.265 编码器输出的同一份 MPEG-TS 字节同时完成两件事：

1. 写入右路 HLS 重封装进程。
2. 送入流式 MPEG-TS/H.265 统计器。

重封装命令使用 `-c:v copy`、`-tag:v hvc1`、`-hls_segment_type fmp4`，生成 `init.mp4` 和 `segment_*.m4s`。播放器使用 `hls.js 1.6.16` 的 MSE 路径；能够原生播放 HEVC HLS 的环境也可使用原生路径。

统计器解析 PAT、PMT 和 PES，找到 H.265 视频 PID，只累计 PES 有效载荷字节，排除 TS 包头、适配字段、PES 头、初始化文件和无关 PID。统计器支持任意输入分片、PES 跨包、异常包恢复和停止时已识别尾部。

## 4. 源码 HLS

源码 HLS 使用完全相同的 H.264 elementary stream 字节与 `-c:v copy` 生成 MPEG-TS 分片。它不重新编码来修正摄像头关键帧间隔。源码和右路都保留 60 个分片，播放列表不缓存、不代理缓冲，HLS 文件响应先读取不可变字节快照。

## 5. 指标口径

正式节省率只使用最近 30 秒 elementary stream 滚动窗口：

```text
bandwidth_saving_pct = (source_h264_bitrate - h265_bitrate)
                       / source_h264_bitrate * 100
```

源码在分发前只计数一次；H.265 使用 MPEG-TS PES 载荷统计。HLS 容器开销不进入 `bandwidth_saving_pct`。负值原样返回并在网页显示为码率增加。

HLS 传输诊断另按当前播放列表中最新约 30 秒、已经封口且文件存在的媒体分片统计：源码接受 `.ts`，右路接受 `.m4s`，`init.mp4` 忽略。口径固定为 `closed_hls_segment_bytes_latest_30s_media_duration`。

## 6. 播放与恢复

源码目标延迟为自身 HLS live edge 前 10 秒，H.265 目标延迟为 15 秒。两路不共享时间轴、不强制同帧。正常播放只在 `0.98x` 到 `1.02x` 范围内靠近各自目标。每一路以自己的 `video.buffered` 计算实际缓冲，低于 1.5 秒时只恢复对应路；达到锁定目标的 8 秒缓冲后再继续播放。

手动播放、暂停、停止、页面关闭同时控制两路。分割线拖动只改变裁剪位置，视频继续按各自状态播放，不暂停、不跳转、不重置播放速率。停止时前端先销毁播放器并等待 250 ms，再请求后端回收。

## 7. HEVC 能力门禁与错误处理

页面读取 `/api/runtime` 后，使用 `MediaSource` 或 `ManagedMediaSource` 依次检测泛化 `hvc1` 以及完整 Main 8-bit codec 字符串，并检查原生 HEVC HLS 能力。任一完整 codec 字符串受支持即可通过启动门禁；全部不支持时，在提交 `POST /api/streams` 前明确提示启用系统 HEVC 解码器。

启动后的 H.265 manifest、MSE、媒体或 codec 错误不触发 H.264 回退，而是立即停止当前会话，回收五个 FFmpeg 进程，并提示启用系统 HEVC 解码器。

## 8. 安全与生命周期

接口只接受 `rtsp_url` 字段。状态、日志和错误不得暴露用户名、密码、完整路径、query 或 fragment。HLS 路由只接受固定 variant 下的单层 `.m3u8`、`.ts`、`.m4s`、`.mp4` 文件名，并验证路径仍在会话目录内。

只允许一个活动会话。状态、HLS 请求和页面心跳共同续租，租约为 45 秒；页面停止、租约超时或进程退出时清理所有管道、线程、进程和会话目录。

## 9. CLI

```text
python -m hevc_lab check-env
python -m hevc_lab web --host 127.0.0.1 --port 8000
```
