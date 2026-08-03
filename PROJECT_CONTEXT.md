# V2.2.1 Remote Stable 项目共享上下文

## 项目位置

- 独立仓库：`C:\Users\31969\Documents\hbc_V2.2_remote_stable`
- 基线：`V2.2` 标签提交 `04580250897ca6f6e4e13d225edf74bfa20c943c`
- 工作分支：`codex/v2.2.1-remote-stable`
- 远端：仅配置 fetch 用 `upstream`，push URL 为 `no_push`
- Python 3.9：`C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe`
- 视频工具：`.tools\ffmpeg\bin\ffmpeg.exe` 与 `.tools\ffmpeg\bin\ffprobe.exe`

该副本通过 `git clone --no-hardlinks` 创建，Git 对象库与原仓库独立。项目内 FFmpeg 已复制，原仓库的 `work/`、`output/` 和浏览器测试产物未复制。

## 当前目标

项目版本为 `2.2.1`。输入固定为 H.264 RTSP 视频，音频忽略。正式处理链保持 V2.2：一条持续 RTSP 连接输出 H.264 elementary stream，Python 只计数一次源字节，再将完全相同的字节送往源码 HLS 重封装和 H.264 解码分支。

源码 HLS 使用 `-c:v copy`。解码后的 `yuv420p` 原始帧通过约 5 秒、最大 512 MiB 的阻塞队列进入固定参数 H.265 编码器。H.265 elementary stream 在预览转换前统计；其后的 H.264 HLS 仅供浏览器观看，不进入节省率。

第一阶段只优化远程观看：右路浏览器预览限制到约 3 Mbps，并把双路共同时间轴改为独立固定延迟与独立缓冲恢复。

## 固定配置

正式 H.265 唯一配置位于 `hevc_lab/config.py`，保持不变：

```text
CRF 36.0 / preset fast / Main 8-bit / yuv420p
ref 4 / bframes 4 / b-adapt 2 / lookahead 45
GOP 10 秒 / min GOP 2 秒 / scenecut 40
cutree 1 / weightp 1
AQ mode 2 / strength 1.0 / qg-size 32 / aq-motion 0
```

右路浏览器预览也由同一模块的独立不可变配置生成：

```text
libx264 / preset ultrafast / CRF 26
maxrate 3M / bufsize 6M / 保持源分辨率
1 秒 GOP / 1 秒 HLS 分片
preview_only=true / included_in_bitrate_comparison=false
```

## 播放与恢复

- 播放策略：`independent_fixed_delay`，不强制同帧。
- 源码路目标 HLS 边缘延迟：10 秒。
- H.265 预览路目标 HLS 边缘延迟：15 秒。
- 正常播放以 `0.98-1.02x` 小幅调速靠近各自目标。
- 每一路只读取自己的 `video.buffered`；实际缓冲低于 1.5 秒时只暂停该路。
- 恢复先锁定目标积累缓冲，避免追逐持续移动的 live edge；随后在当前固定延迟目标达到至少 8 秒实际缓冲后恢复。
- 某一路播放窗口失效时，只重新定位该路。
- 手动播放、暂停、停止和页面关闭控制两路；拖动分割线期间冻结双路画面。
- 停止时前端先销毁两路播放器并等待 250 ms 完成请求取消，再调用后端统一回收。

## 指标口径

码率节省证据保持：

`source_h264_elementary_stream_bytes_vs_h265_elementary_stream_bytes_rolling_30s`

后端另外从当前 HLS 播放列表中，按最新约 30 秒已封口且存在的 TS 分片统计实际传输字节、媒体时长和码率，固定口径为：

`closed_ts_segment_bytes_latest_30s_media_duration`

浏览器按最近 10 个完整分片的实际下载字节和耗时计算加权下载速度。下载速度除以 HLS 实际传输码率得到带宽余量：小于 `1.0x` 为带宽不足，`1.0-1.3x` 为余量偏紧，不低于 `1.3x` 为余量充足。浏览器缓冲、下载速度、余量和卡顿次数不写回后端。

HLS 路由对播放列表和 TS 都先读取不可变字节快照再响应，避免 FFmpeg 文件轮转、远程慢传输或停止清理造成 `Content-Length` 竞态。统一回收顺序为先 terminate/wait/kill 五个子进程，再关闭 stdin/stdout/stderr，防止带缓冲 stdin 在关闭 flush 时阻塞。

## 当前边界

- 第一阶段不修正“声明 20 fps、实际约 15 fps”等时间基准问题。
- 第一阶段不保留 RTSP 原始 PTS，也不重构共享阻塞背压管线。
- 不增加降噪，不改变正式 H.265 编码方案和码率比较证据。
- 页面延迟仅表示相对各自 HLS live edge 的距离。

## 已验证事实

- Python 编译、前端语法和环境检查通过；常规测试发现 23 项，其中 22 项通过、120 秒长测默认跳过，长测已显式单独执行通过。
- 1080p/20fps 五进程管线连续运行 129.45 秒：源码 HLS `0.756 Mbps`，右路 HLS `3.089 Mbps`，结束队列 `0/100`，五个进程全部退出。
- Playwright 在 80 ms RTT、5 Mbps 下完成 120 秒稳定窗口：启动后两路卡顿增量均为 0，24 次采样均未暂停。
- 右路分片中断时右路独立暂停，源码继续；低缓冲注入时只有对应路线暂停，并在实际缓冲恢复到约 10 秒后继续。
- 桌面 1440x1000 与移动 390x844 无横向溢出、文字裁切或布局重叠；两路均为 1920x1080 并持续播放。
- 正常启动与停止回归的浏览器控制台为 0 errors / 0 warnings；停止后两路 `readyState=0`，无本次测试服务或 FFmpeg 残留。
- 120 秒浏览器样本中 HLS 边缘延迟仍会因现有媒体时间基准漂移，属于第一阶段明确保留的后续问题。
