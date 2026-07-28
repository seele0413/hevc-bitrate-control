# H.265 V1.6 开发任务

最后更新：2026-07-28

## 当前状态

v1.6.0 当前正式入口只输出两路结果：`default_h264.mp4` 和 `hevc_fixed.mp4`。H.264 原生编码只指定 `libx264`；H.265 固定参数方案使用 `CRF 36.0 · preset medium · GOP 10s · ref 6 · b-frames 8 · lookahead 90 · 无roi · 无降噪`。

V1.6 不做 CRF 搜索、不使用 ROI、不使用降噪、不评选胜出方案、不生成部署结论。旧 V1.4 预算中性 ROI 和旧四路输出均降级为历史研究内容。

## 已完成

- [x] 将项目版本更新为 `1.6.0`。
- [x] 将 `configs.py` 正式 `multi-encode` 方案收敛为一个 H.265 固定参数方案，并保留 H.264 原生编码基准。
- [x] 新增 `libx264` 原生默认编码路径：只指定 `libx264`，不传自定义 CRF、preset 或编码参数。
- [x] 将 `multi-encode` 正式流程改为完整参考输入上生成 `default_h264` 与 `hevc_fixed` 两路结果。
- [x] 将报告、Web 任务阶段、下载白名单、CLI 描述和静态 demo 数据改为 V1.6 两路输出。
- [x] 调整静态与实时 demo 的蓝色参数标签样式：标签紧贴分割竖线右侧，接近右边界时自然裁切，不拉伸。
- [x] 更新 V1.6 单元测试预期。
- [x] 将本地实时入口改为 `apps/web` 单 RTSP 输入双路二次编码预览：H.264 原生参数编码与 H.265 固定参数优化编码。
- [x] 将实时链路改为单一持续 RTSP 解码，并通过容量为 2 的共享帧队列向两路原生编码器分发同一组画面帧。
- [x] 在预览转码前统计 H.264/H.265 原生编码字节，并按最近 30 秒窗口计算实时码率和节省率。
- [x] 将两路原生码流分别解码后，用同一套 H.264 低延迟参数生成浏览器等价预览，移除浏览器 HEVC fMP4/HLS 依赖。
- [x] 新增播放器 500 ms 同步校正、2 秒页面心跳、页面关闭停止信标和后端 10 秒租约回收。
- [x] 移除实时 FFmpeg 的隐藏窗口标志，服务退出、页面停止或租约超时时统一回收整组子进程。

## 待完成

- [x] 使用合成运动源完成一次实时管道端到端实验，两路均生成 H.264 HLS 等价预览并返回原生码率与节省率。
- [ ] 使用真实固定监控片段完成一次 V1.6 端到端实验，并把长期有效事实写入 `PROJECT_CONTEXT.md`。
- [ ] 用浏览器截图复核 `apps/demo` 与 `apps/web` 在桌面和移动宽度下的分割线标签裁切效果。
- [ ] 使用真实 RTSP 地址连续观察 `apps/web` 的画面、丢帧、编码速度与延迟，确认等价预览在目标分辨率上能持续实时运行。

## 本次验证记录

- [x] Python 编译检查通过。
- [x] `node --check` 检查 `apps/demo/app.js` 与 `apps/demo_live/app.js` 通过。
- [x] `apps/demo/data/results.json` JSON 语法检查通过。
- [x] `python -m unittest discover -v` 通过，当前 132 项单元测试全部通过。
- [x] 合成 320x180@10fps 运动源通过完整实时管道，两路 H.264 HLS 预览均生成，原生 H.264/H.265 码率与节省率均可读取，停止后无残留合成测试 FFmpeg。
- [x] Playwright 打开 `apps/demo` 本地 HTTP 页面，确认蓝色参数标签 `white-space: nowrap`、`max-width: none`，在 92% 和 98% 分割位置均超出舞台右边界并由 `.stage { overflow: hidden; }` 自然裁切。
- [ ] 环境检查与真实端到端未通过：当前工作树缺少 `.tools/ffmpeg/bin/ffmpeg.exe` 与 `ffprobe.exe`，`python -m hevc_lab check-env` 报告缺少 `ffmpeg、ffprobe`，且没有 `samples/` 输入样本目录。

## 当前禁止事项

- 不恢复旧版 V2/V2.3 网页、MediaMTX、WebRTC 或动态码率架构。
- V1.6 正式入口不得重新加入通用无 ROI、预算中性 ROI 或 ROI + 降噪三路正式输出。
- 不使用手工目标码率比例伪造结果。
- 不评选胜出方案，不把软件编码结果表述成摄像头实机部署结论。
- 不把上传 MP4/MKV 称为传感器原始帧。
