# V2.2.2 HEVC 直接播放项目上下文

## 项目位置

- 独立仓库：`C:\Users\31969\Documents\hbc_V2.2_remote_stable`
- 工作分支：`codex/v2.2.2-hevc-direct`
- 基线：V2.2.1 Remote Stable
- Python：`C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe`
- 视频工具：`.tools\ffmpeg\bin\ffmpeg.exe` 与 `.tools\ffmpeg\bin\ffprobe.exe`

## 当前目标

项目只处理 H.264 RTSP 视频。输入不含音频处理，也不接受 HEVC 或其他视频编码作为隐式转换输入。单会话中保留五个 FFmpeg 进程：RTSP 输入、源码 HLS、H.264 解码、H.265 编码和 H.265 fMP4 HLS 重封装。

数据流为：

```text
H.264 RTSP
  -> H.264 elementary stream
  -> Python 计数一次
     -> 相同字节 -> 源码 MPEG-TS HLS
     -> 相同字节 -> H.264 解码 -> yuv420p 阻塞队列
        -> 唯一 libx265 -> 带时间戳 MPEG-TS
           -> 相同字节 -> H.265 fMP4 HLS stream copy
           -> PAT/PMT/PES 统计 H.265 有效载荷
```

`hevc_lab/web/mpegts.py` 的统计器按 TS 包恢复边界，解析 PAT/PMT 找到 `stream_type=0x24` 的视频 PID，跨包组装 PES，排除 TS/PES 容器头和无关 PID。编码器输出的 MPEG-TS 字节与 HLS 重封装输入完全相同。

## 固定配置

正式 H.265 唯一配置在 `hevc_lab/config.py`：CRF 36.0、preset fast、Main、`yuv420p`、ref 4、B 帧 4、b-adapt 2、lookahead 45、GOP 10 秒、min GOP 2 秒、scenecut 40、cutree、weightp、AQ2、qg-size 32、aq-motion 0。

直接 HLS 配置同样由该模块提供：`hvc1`、fMP4、`init.mp4`、10 秒目标分片和 `.m4s` 分片。

## 接口与指标

- 版本：`2.2.2`，管线版本：`v2.2.2`。
- variant 保持 `source` 与 `h265_optimized`，播放列表字段保持不变。
- `runtime.live_preview.h265_delivery_mode` 为 `timestamped_mpegts_to_hevc_fmp4_hls_stream_copy`。
- `runtime.live_preview.hls_segment_types` 为 `source=mpegts`、`h265_optimized=fmp4`。
- `runtime.live_preview.h265_keyframe_bound_segments` 为 `true`。
- `saving_basis` 为 `source_h264_elementary_stream_bytes_vs_h265_elementary_stream_bytes_rolling_30s`。
- HLS 传输口径为 `closed_hls_segment_bytes_latest_30s_media_duration`。
- `init.mp4` 只提供 fMP4 初始化，不计入 HLS 媒体传输统计，也不计入正式节省率。

## 播放与稳定性

源码路和 H.265 直接播放路使用独立固定延迟，目标分别为 10 秒和 15 秒；低于 1.5 秒只暂停对应路，在当前目标重新获得 8 秒缓冲后恢复。手动播放、暂停、停止和页面关闭仍同时控制两路。分割线拖动只改变裁剪位置，视频继续播放，不暂停、不跳转、不重置播放速率。HLS 请求、状态请求和心跳共同续租，租约为 45 秒。

浏览器启动前必须检测 `MediaSource` 或 `ManagedMediaSource` 对泛化 `hvc1` 或完整 Main 8-bit codec 字符串（例如 `hvc1.1.6.L93.B0`、`hvc1.1.6.L120.B0`）的能力，或者原生 HEVC HLS 能力。启动后出现右路 codec 错误时立即回收整组进程并提示启用系统 HEVC 解码器；不提供 H.264 回退。

## 已验证事实

- `check-env` 已通过，当前 FFmpeg 可用 `libx265`、H.264 解码、MPEG-TS 输入/输出和 HLS。
- MPEG-TS/H.265 统计器已覆盖分片读取、PAT/PMT、PES 跨包、无关 PID、异常包、无界 PES 停止尾部和非法 PES 流 ID。
- 短时真实 FFmpeg 合成流已生成源码 `.ts`、右路 `EXT-X-MAP`、`init.mp4` 和非零时长 `.m4s`，FFmpeg 可从右路 HLS 播放列表解码。
- 当前 Chrome 的泛化 `hvc1` 检测返回 false，但完整 Main codec `hvc1.1.6.L93.B0`、`hvc1.1.6.L120.B0` 返回 true；前端已兼容常见完整 codec 字符串。Chromium 合成页面实测可提交 RTSP、进入 running，控制台 0 errors/0 warnings，手动停止后两路均为 stopped 且无错误。Edge 桌面/移动布局仍需在相同 codec 检测路径下完成实际播放验收。
