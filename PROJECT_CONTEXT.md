# V2.1.0 项目共享上下文

## 项目位置

- 当前实体目录：本文档所在项目根目录
- Python 3.9：`C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe`
- 视频工具：`.tools\ffmpeg\bin\ffmpeg.exe` 与 `.tools\ffmpeg\bin\ffprobe.exe`

## 当前目标

项目版本为 `2.1.0`，当前唯一入口是本机实时 Web 工具。输入固定为 H.264 RTSP 视频，音频忽略。

一个持续 RTSP 连接输出 H.264 elementary stream。Python 按收到的字节只统计一次，然后把完全相同的字节同步送到源码 HLS 重封装进程与 H.264 解码进程。源码 HLS 使用 `-c:v copy`，不重新编码。

解码后的 `yuv420p` 原始帧通过连续性优先有界队列，只进入 H.265 固定参数编码器。H.265 elementary stream 在预览转换前统计，再由 `libx264` 生成仅供浏览器观看的 HLS；该预览码率不进入比较。

## 固定配置

H.265 唯一配置位于 `hevc_lab/config.py`：

```text
CRF 36.0
preset fast
Main 8-bit / yuv420p
ref 4 / bframes 4 / b-adapt 2 / lookahead 45
GOP 10 秒 / min GOP 2 秒 / scenecut 40
cutree 1 / weightp 1
AQ mode 2 / strength 1.0 / qg-size 32 / aq-motion 0
```

编码命令和状态接口都从该配置对象生成。

## 实时约束

- 单会话限制。
- 队列目标约 5 秒，原始帧内存上限 512 MiB。
- 队列满时阻塞，不主动丢帧。
- 页面每 2 秒心跳；后端 10 秒未收到心跳时停止会话。
- 任一 FFmpeg 分支退出时停止并回收整组进程。
- 源码 HLS 依赖摄像头关键帧切片；超过 8 秒未生成播放列表时只告警，不重新编码修正。
- HLS 默认保留 20 个分片，两路积累约 5 秒后共同起播。
- RTSP 对外只显示脱敏主机；凭据、完整路径、query 与 fragment 不进入日志或状态。

## 公开接口

- CLI：`check-env`、`web`
- HTTP：`/api/health`、`/api/runtime`、`/api/streams`、流查询/停止/心跳与 HLS 文件
- 实时 variant：`source`、`h265_optimized`
- 比较基准：`source_h264_elementary_stream_bytes_vs_h265_elementary_stream_bytes_rolling_30s`

现有 `work/` 用户结果保留，但 V2.1.0 不再读取旧结果。

## 已验证事实

- Python 3.9 编译检查和 16 项实时测试通过。
- 项目内 FFmpeg 能力检查通过。
- 合成 H.264 elementary stream 经过真实五进程管线后，两路 HLS 均生成且已封口分片可完整解码。
- 源码与 H.265 两路滚动字节和码率可读取，停止后进程与管道全部回收且无资源告警。
- Playwright 在 1440x1000 与 390x844 视口确认两路视频完整加载、同步播放，页面无横向溢出、元素重叠或控制台错误。
- 浏览器实测编码积压在修正结构化进度解析后按帧数正常更新，并与浏览器播放延迟分开展示。
- 实时页面已采用视频优先操作台布局；固定参数直接由运行时唯一配置生成。
- 分割线拖动使用 Pointer Events 与动画帧提交，拖动期间冻结双路播放；停止操作会在后端回收完成前立即卸载浏览器播放器。
