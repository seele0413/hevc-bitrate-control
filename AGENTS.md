# Codex 项目协作规则

## 开始工作前

每个新对话在分析、设计或修改代码前，必须依次完整阅读：

1. `PROJECT_CONTEXT.md`
2. `DESIGN_V1.md`
3. `TASKS.md`
4. `README.md`

如果代码与文档冲突，以 `DESIGN_V1.md` 当前方案和 `TASKS.md` 任务状态为准，并先说明冲突。

## 当前唯一方向

- 项目版本为 V2.2.2 HEVC 直接播放实时工具。
- 只接受 H.264 RTSP 视频，不处理音频，也不对其他视频编码隐式转换。
- 单个 RTSP 输入进程输出 H.264 elementary stream；Python 只计数一次源字节，再把完全相同的字节送往源码 HLS 和 H.264 解码分支。
- 源码路使用 H.264 `stream copy` 生成 MPEG-TS HLS。
- 解码后的 `yuv420p` 通过约 5 秒、最大 512 MiB 的阻塞队列进入唯一固定参数 `libx265` 编码器。
- 正式 H.265 编码器输出带时间戳 MPEG-TS；同一份 MPEG-TS 字节同时送往右路 HLS 重封装，并由流式 PAT/PMT/PES 统计器计数 H.265 PES 有效载荷。
- 右路 HLS 只使用 `-c:v copy`、`hvc1` 和 fMP4，不存在 `libx264` 预览编码或 H.264 回退。
- 仍保持五个 FFmpeg 进程、单会话、45 秒租约和任一分支失败时的统一回收。
- 浏览器源码路目标延迟 10 秒，H.265 直接播放路目标延迟 15 秒；两路独立恢复，不强制同帧。
- 只保留 `check-env` 与 `web` 两个 CLI 命令。

## 结论边界

- `bandwidth_saving_pct` 只比较 H.264 与 H.265 elementary stream 最近 30 秒滚动窗口，不计 HLS 容器开销。
- HLS 传输指标使用 `closed_hls_segment_bytes_latest_30s_media_duration`，源码统计 `.ts`，右路统计 `.m4s`，忽略 fMP4 `init.mp4`。
- 负的 `bandwidth_saving_pct` 必须原样返回并显示为码率增加。
- RTSP 基准播放滞后、H.265 编码积压和 HLS 传输开销必须分别表达。
- 本机软件编码结果不得表述为摄像头硬件编码器验证结果。
- 源码直通是 H.264 压缩码流不重新编码，不得称为传感器原始帧。

## 开发约束

- 默认使用中文说明、注释和界面，代码标识符使用英文。
- 使用 Python 3.9 与项目内 `.tools/ffmpeg`、`.tools/ffprobe`。
- 正式 H.265 参数只来自 `hevc_lab/config.py`；直接 HLS 投递参数也只从该配置读取。
- 队列满时阻塞上游，不主动丢帧。
- HLS 保留 60 个分片；右路分片按正式 GOP 关键帧边界生成，实际时长可以接近 10 秒。
- HLS 播放列表禁止缓存和代理缓冲；`.m3u8`、`.ts`、`.m4s`、`.mp4` 路由必须校验单层文件名和会话目录边界。
- RTSP 日志和状态必须删除用户名、密码、完整路径、query 与 fragment。
- 不删除 `work/` 中已有用户结果；新程序不读取历史离线结果。
- 修改完成后运行 Python 编译、全部单元测试、`check-env`、真实 FFmpeg 集成测试、JavaScript 语法检查和浏览器桌面/移动验证。
- 完成任务后更新 `TASKS.md`；长期事实同步更新 `PROJECT_CONTEXT.md`。
