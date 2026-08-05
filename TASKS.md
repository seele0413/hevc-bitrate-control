# V2.2.2 HEVC 直接播放任务

最后更新：2026-08-05

## 已完成

- [x] 从 V2.2.1 Remote Stable 创建 `codex/v2.2.2-hevc-direct`，主项目保持不变。
- [x] 删除浏览器 `libx264` 预览配置、命令和环境检查。
- [x] 保持唯一正式 `libx265` 配置、五进程管线、阻塞队列、45 秒租约和统一进程回收。
- [x] 将 H.265 编码器输出改为带时间戳 MPEG-TS。
- [x] 增加流式 MPEG-TS/H.265 统计器，解析 PAT/PMT、视频 PID、跨包 PES、无关 PID、异常包和停止尾部。
- [x] 将同一份 MPEG-TS 字节送入 H.265 fMP4 HLS 重封装和 PES 统计器。
- [x] 右路 HLS 使用 `-c:v copy`、`hvc1`、fMP4、`init.mp4` 和 `.m4s`，按关键帧切片。
- [x] HLS 路由允许 `.m3u8`、`.ts`、`.m4s`、`.mp4`，并返回正确媒体类型。
- [x] HLS 传输统计扩展为源码 `.ts` 与右路 `.m4s`，忽略 fMP4 初始化文件。
- [x] 更新 V2.2.2 runtime/status 字段、前端标签、播放配置和 `hls.js 1.6.16`。
- [x] 增加提交 RTSP 前的 HEVC MSE/原生 HLS 能力门禁，兼容完整 Main codec 字符串；运行中 codec 错误停止会话，不提供回退。
- [x] 修正手动停止按钮把 PointerEvent 当作错误原因的问题，正常停止显示 `stopped` 且不产生伪错误。
- [x] 移除分割线拖动冻结：拖动只改变裁剪位置，两路视频继续按各自状态播放。
- [x] 更新命令、网页、集成测试和项目技术文档。

## 验证状态

- [x] Python 编译检查通过。
- [x] JavaScript 语法检查通过。
- [x] MPEG-TS/PES 单元测试通过，覆盖 5 项解析场景。
- [x] 常规单元与接口测试通过，当前 28 项通过，120 秒长测需单独执行。
- [x] `check-env` 通过，FFmpeg 具备 libx265、H.264 解码、MPEG-TS 输入/输出和 HLS。
- [x] 短时真实 FFmpeg 集成测试通过：源码 `.ts`、右路 `EXT-X-MAP`、`init.mp4`、非零 `.m4s` 和 FFmpeg H.265 HLS 解码均正常。
- [x] 1080p/20fps 五进程 120 秒长测：源码 HLS 约 0.743 Mbps，H.265 HLS 约 1.379 Mbps，队列 0/100，停止后五进程全部退出。
- [x] Chromium 与 Edge 桌面/移动 Playwright 验证：无横向溢出、无控制台错误；当前 Chrome 泛化 `hvc1` 为 false、完整 Main codec 为 true，修正后合成页面可提交 RTSP、进入 running，并在停止后两路均为 stopped。
- [x] 同一 `320x180/10fps` 合成流的 30 秒网页传输诊断：V2.2.1 H.264 预览 HLS 约 0.290 Mbps，V2.2.2 H.265 fMP4 HLS 约 0.060 Mbps，下降约 79.18%；该结果不写入正式节省率。
- [x] Chromium `160x90/2fps` 合成页面拖动验收：两路播放时拖动不改变 `paused` 状态，拖动期间 `currentTime` 持续前进，播放速率不被重置；手动暂停后拖动仍保持暂停，停止后两路 `readyState=0`，控制台 0 errors / 0 warnings。
- [ ] 在安装系统 HEVC 解码能力且 hvc1 MSE 可用的 Chromium 与 Edge 环境完成双路实际播放、独立恢复、停止回收和分割线交互验收。

## 边界

- 不修改主项目 V2.3.0。
- 不修改正式 H.265 参数，不新增第二个 H.265 编码器，不恢复 H.264 回退。
- 不删除 `work/` 中已有用户结果。
- 不提交、不打 tag、不推送 GitHub，除非另有明确请求。
