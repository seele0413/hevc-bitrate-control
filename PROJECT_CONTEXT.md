# H.265 课题共享上下文

## 项目位置

- 当前实体目录：`F:\work\project_hevc-bitrate-control\h265-mvp`
- 兼容路径：`C:\Users\31969\work\课题\h265-mvp`，该路径指向 F 盘实体目录。
- Python 3.9：`C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe`
- 项目内工具：`.tools\ffmpeg\bin\ffmpeg.exe`、`.tools\ffmpeg\bin\ffprobe.exe` 和 `.tools\vmaf`。

## 当前课题目标

课题研究固定监控画面的 H.265 低码率编码。当前阶段不直接做实时网关转码，而是先用可重复实验回答：

> 对同一个输入视频，x265 原生默认编码、通用无 ROI 方案、预算中性 ROI 方案和 ROI + 降噪实验项分别会得到怎样的分辨率、平均视频包码率、CRF、客观画质、相对默认码率节省、相对通用无 ROI 方案节省，以及 ROI 是否能在预算内保护或改善重点区域？

第一版的正式设计见 `DESIGN_V1.md`。

## 已废弃历史

- 2026-07-22 按用户明确要求，原 `h265-mvp` 中的旧版源码、网页、RTSP/MediaMTX、动态码率模块、历史结果、旧样本和 Git 历史已全部删除且未备份。
- 旧 V1、V2、V2.1、V2.2、V2.3 的命令、页面和架构不再可用，也不是当前开发依据。
- 仅通用 FFmpeg、FFprobe 和 VMAF 工具被重新放入新工程；未继承旧业务代码。

## 当前代码状态

项目已经从零建立 `hevc_lab` 固定 CRF 实验原型，现有能力包括：

- 检查 libx265、libvmaf 和 VMAF 模型。
- 生成静态—运动—静态合成样本。
- 在相同 CRF、preset 和统一参考帧下测试工程基线与帧间优化两套正式参数。
- 输出视频码率、VMAF 平均值、VMAF P5、SSIM、编码速度、CSV、JSON 和中文摘要。
- 区分“相对 x265 工程基线的参数收益”和“相对输入源流的部署收益”。
- 相对源流未节省至少 5% 时判定原码流直通，不生成 `selected_*.mp4`。
- 已按 `core`、`encoders`、`metrics`、`adapters`、`reports` 和 `web` 重整模块边界；顶层 `models`、`probe` 和 `report` 保留兼容导入，现有命令行为不变。
- 已实现参考片段开始时间和时长选择、PTS 归零、源帧率/yuv420p 统一、FFV1 无损缓存、输入 SHA256、帧数及逐帧时间戳摘要校验。
- 已固化 `libx265 + Main 8-bit + yuv420p` 统一编码条件。历史双方案命令仍保留工程基线与优化组合；当前 `multi-encode` 正式入口使用 x265 原生默认作为比较基准。
- 已实现用户可选的保守 `conservative`、综合 `balanced`、激进 `aggressive` 三种帕累托模式。三种模式统一管理默认 CRF、优化参数、质量门槛、算法节省门槛和源流节省门槛；综合模式保持原 4 秒优化组合并作为默认。
- 已实现单方案 CRF 18～38、0.5 精度的自适应画质搜索：先测 18/28/38，二分缩小边界，复核最终点两侧相邻点；检测到明显非单调时补测完整 41 点。
- 搜索候选按输入 SHA256、参考缓存键、完整编码参数、CRF 和 VMAF 模型哈希缓存；已有 `search-crf` 开发命令及 JSON、CSV、Markdown 搜索报告。
- 已实现工程基线与优化组合共享同一参考片段的独立搜索，并在每路最高合格 CRF 及其低一档相邻点中执行肉眼无损近似配对；两路先过绝对画质门槛，再要求 `|ΔVMAF|≤1.0`，否则返回“证据不足”。
- 已新增 `pair-crf` 开发命令和 `pair_search.json`、`pair_quality_points.csv`、`pair_search_summary.md` 报告。
- 已实现质量驱动 Capped CRF/VBV 研究：先搜索自然 CRF 边界，再按三模式峰值倍率和缓冲时长测试局部码率保护；画质失败自动放宽，只有同时保持画质并改善码率才选择。
- 平均码率已统一为 `v:0` 视频压缩包字节总和×8÷时长，并新增固定1秒时间窗峰值和P95；容器声明码率、音频和封装开销不进入核心比较。
- 已新增 `rate-control` 开发命令和 JSON、CSV、Markdown 码控报告。
- 已实现 AQ 自适应量化研究：显式默认 AQ2 对照，暗场 AQ3 和边缘 AQ4 按三模式独立搜索 CRF 边界并执行等画质配对；只有画质、速度和码率收益同时通过才采用，否则回退 AQ2。
- 已新增 `aq-study` 开发命令和 `aq_study.json`、`aq_quality_points.csv`、`aq_study_summary.md`，AQ 参数进入候选缓存键。
- 已实现固定机位静态 ROI 研究：通用 JSON 配置校验、三模式 QP 偏移、FFmpeg `addroi`/libx265 16×16 块级量化偏移、无 ROI/ROI 独立 CRF 搜索、critical/evidence 局部画质复算和无收益回退。
- 已新增 `roi-study` 命令、入口摄像头 1920×1080 配置和 `roi_study.json`、`roi_quality_points.csv`、`roi_region_quality.csv`、`roi_study_summary.md`、`roi_overlay.png`。
- 已实现 ROI 保护的区域降噪研究：三模式 `hqdn3d` 强度统一配置，以 normal 底图按 discard、critical、evidence 顺序覆盖，时间戳原图直通；无降噪/降噪独立 CRF 搜索并按全局、局部、速度和视频包码率决定采用或回退。
- 已新增 `denoise-study` 命令和 `denoise_study.json`、`denoise_quality_points.csv`、`denoise_region_quality.csv`、`denoise_study_summary.md`、`denoise_overlay.png`；降噪配置进入候选缓存键。
- 已实现三种正式结论：算法可行性、软件画面连续性和部署可行性初筛。连续性会执行全文件解码、规格/帧数/时长和逐帧时间戳检查，编码速度0.97x为持续处理门槛，1.0x以上额外标记当前电脑可实时处理。
- `pair_search.json` 与 `pair_search_summary.md` 已输出结构化结论、逐项检查、实际值、门槛、失败原因和适用边界。
- 已新增正式 `compare` 命令，串联参考准备、双路CRF搜索、等画质配对、连续性检查和三结论；`comparison_state.json` 原子记录请求哈希、阶段、尝试和错误，`comparison.json` 保存完整结果。
- 实验缓存键覆盖输入内容、VMAF模型、程序/流水线版本、片段范围、模式及全部有效门槛。中断/失败后复用参考和候选缓存，完整结果且候选文件仍存在时直接短路返回；参数变化不会命中整次实验缓存。
- 当前 V1.4 `multi-encode` 正式入口输出 `default_x265`、`generic_no_roi`、`budget_neutral_roi`、`roi_denoise_experimental` 四路结果；V1.3 保守/综合/激进和 V1.2 激进三档仅作为历史内部结果保留。
- 通用无 ROI 方案是 ROI 预算基准：复用 V1.0 原激进帧间结构，`medium` preset，启用 AQ2，但不读取 ROI 配置、不生成 `addroi`、不生成 `hqdn3d` 分区降噪，也不因固定 ROI 分辨率不匹配失败。
- 预算中性 ROI 方案在通用无 ROI 平均视频包码率预算内，仅使用 aggressive 静态 ROI QP 重新分配码率。
- ROI + 降噪实验项在同一预算内叠加 aggressive 静态 ROI QP 和 ROI 保护分区降噪；它是实验项，不得默认表述为收益。
- V1.4 三个非默认策略统一画质门槛为 VMAF≥83、P5≥80、SSIM≥0.950；ROI 两路还必须满足平均视频包码率≤通用无 ROI 方案，并且 critical/evidence 重点区域局部 VMAF、P5、SSIM 不低于通用无 ROI 方案。
- 报告固定四行，展示分辨率、平均视频包码率、CRF、VMAF/P5/SSIM、编码速度、相对默认节省、相对通用无 ROI 节省、预算中性和 ROI 局部保持/改善；不输出目标区间、不评选胜出方案、不生成部署结论。
- 现有 115 项单元测试通过，libx265/libvmaf/addroi/hqdn3d 环境检查通过，V1.4 真实 60 秒四路回归通过；已生成的默认与通用无 ROI MP4 完整解码退出码均为0。
- 已用 12 秒合成样本完成两套正式参数的端到端回归：300 帧、首帧 PTS=0，优化组合相对工程基线节省 2.32%。

当前原型的关键不足：

- 两方案搜索、配对、视频包字节口径、三种正式可行性结论、`compare` 编排和当前四路 `multi-encode` 报告已经实现；长期跨片段平均码率反馈尚未实现。
- 当前优化组合同时改变了多个帧间参数，只能判断组合整体是否有效，不能作为严格的单参数消融结论。
- 当前只实现固定坐标 ROI，没有动态人员/车辆检测、跟踪、语义 ROI 和隐私遮挡。
- 当前降噪只覆盖随机空域/时域噪声；雨雪、烟雾、光线闪烁、过度锐化振铃和摄像头增益/曝光控制尚未实现。
- 当前本地 Web 入口挂载 `apps/demo_live`，视觉复用 `apps/demo` 的居中卡片、通用/专用 Tab、双视频重叠画面和拖动分割线；主流程为 RTSP 实时双画面对比。
- 当前本地 Web 页面新增 RTSP 实时双预览：用户输入原生 H.264 RTSP 与 H.265 保守策略 RTSP 两路地址后，FastAPI 后端用 FFmpeg 拉流并输出 `source` 与 `conservative` 两路浏览器可播 HLS，前端通过本地 `hls.js` 播放两路画面；后端另行以 copy 方式统计两路摄像头输入码流的实时视频包码率，页面带宽节省只按输入码流估算，不使用 H.264 HLS 预览码率。浏览器不能直接播放 `rtsp://`，H.265 预览需要在本机转为 H.264 HLS，这只影响预览延迟和本机/局域网资源，不改变摄像头侧上行带宽口径。
- `apps/demo` 仍是独立 Cloudflare Pages 静态展示版：只使用原生 HTML/CSS/JS 和 `data/results.json`，从 `videos/` 播放 H.264 预览并提供 H.265/HEVC 下载；不包含上传、FastAPI、Python、FFmpeg、后端任务队列或在线编码能力，可按 Cloudflare Pages `Framework preset=None`、`Build output directory=apps/demo` 部署。
- 当前只能证明软件 libx265 条件下的参数收益，不能证明摄像头硬件侧已经有效。

## 已获得的实验事实

### 合成样本

- 输入为 12 秒、640×360、静态—运动—静态的无损参考视频。
- 固定 CRF 22 下，监控组合参数 VMAF 97.790、VMAF P5 97.428、SSIM 0.998903。
- 相对同条件工程基线码率降低 2.32%。
- 三模式固定 CRF 回归均成功：保守模式算法节省 -0.21%，综合模式节省 2.32%，激进模式节省 1.59%。三者都未达到各自部署门槛，因此均回退为原码流直通，未生成 `selected_*.mp4`。
- 综合模式优化方案完成 CRF 18～38 自适应搜索：按 18、28、38、33、30.5、29、29.5、28.5 的顺序测试 8 点，最终选择 CRF 29.0；该点 VMAF 96.700、P5 93.427、SSIM 0.996581、视频码率约 0.157 Mbit/s。CRF 29.5 因 P5 92.990 低于 93 被拒绝，左右相邻点已经复核。
- 相同命令第二次执行时，8 个候选及指标全部命中缓存；该结果仍只是单方案画质边界，不能宣布算法节省成功。
- 综合模式双方案搜索各测试 8 点：工程基线最高合格 CRF 29.5，优化组合最高合格 CRF 29.0。边界候选最终配对为 CRF 29.0 对 29.0，基线 VMAF 96.712、0.162 Mbit/s，优化组合 VMAF 96.700、0.157 Mbit/s，`|ΔVMAF|=0.012`，配对点码率降低 3.19%。第二次运行 16 个候选全部命中缓存，约 1.2 秒完成。
- 上述 3.19% 只是任务 5 的配对事实；综合模式要求的 5% 算法节省门槛将在任务 7 中正式判断，目前不得宣布算法可行性通过。
- 综合模式优化组合的质量驱动码控实验以 CRF 29.0 无上限结果为基准：平均 0.157 Mbit/s，1秒峰值 0.469 Mbit/s。1.50×与1.75×峰值倍率虽分别降低平均码率24.81%和12.78%，但P5只有90.313和92.343，均被拒绝；自动放宽到2.00×（maxrate 315 kbit/s）后，VMAF 96.624、P5 93.369、SSIM 0.996383，平均码率降低2.37%、1秒峰值降低0.66%，因此成为首个质量保持候选。
- 视频包口径验证：同一H.265视频流封装为纯视频MKV与附加AAC音频MKV后，文件大小分别235,432和343,278字节，但两者 `v:0` 视频包字节均为230,269，证明音频和容器开销未进入核心码率。

### 真实监控片段

- 输入为真实 1080p、20 fps 固定监控录像的 12 秒片段。
- 工程基线：1.795 Mbit/s，VMAF 96.019。
- 帧间优化正式组合：1.584 Mbit/s，VMAF 96.176、VMAF P5 95.584、SSIM 0.996212，软件编码速度 1.63x。
- 相对同条件工程基线降低 11.77%，说明正式帧间优化组合在该样本上存在固定 CRF 压缩效率收益。
- 但候选相对约 0.504 Mbit/s 的输入源流增加 214.37%，因此部署决定为原码流直通，且没有生成可部署视频。
- 该结果只是单片段、固定 CRF 的前期证据，不能作为最终等画质结论。

### 真实监控素材完整 60 秒回归

- 2026-07-22 使用 `F:\work\课题\监控素材.mp4` 完成完整 60 秒、1200 帧回归；源流为 1920×1080、20 fps、H.264，视频包平均码率 0.491 Mbit/s、1 秒峰值 0.744 Mbit/s，文件大小 3,706,763 字节。
- 综合模式两方案独立搜索后，肉眼无损近似配对为工程基线 CRF 25.0 对优化组合 CRF 25.5。基线 VMAF 95.177、P5 93.600、SSIM 0.993975、平均 1.343 Mbit/s、速度 2.162x；优化组合 VMAF 95.199、P5 93.713、SSIM 0.993970、平均 1.129 Mbit/s、速度 1.752x。
- 配对点 `|ΔVMAF|=0.022`，优化组合相对同画质工程基线降低平均码率 15.94%，说明该帧间参数组合在本素材的软件 libx265 对照中存在明显算法收益。
- 优化组合相对输入源流的平均码率不是降低而是增加 129.72%，文件大小由 3,706,763 增至 8,483,683 字节；源流 1 秒峰值 0.744 Mbit/s，优化输出峰值 3.145 Mbit/s。因此当前素材的部署决定仍应为原 H.264 码流直通，不能把算法收益等同于部署收益。
- 质量驱动 VBV 试验以优化 CRF 25.5 为无上限基准。1.50× 上限使 1 秒峰值降低 2.07%，但平均码率反而增加 6.99%；1.75×～2.50× 同样没有平均码率收益。所有候选画质均通过，但没有候选同时改善平均码率和峰值，程序正确回退到无上限 CRF。
- 优化输出已完成全文件解码检查，无解码错误。完整结果位于 `results/monitor-60s-balanced-rate-control`。
- 任务7正式结论回归：算法可行性通过，等画质节省15.94%，超过综合模式5%门槛；软件画面连续性通过，输出1920×1080、20 fps、1200帧、60秒，首帧PTS为0、最大帧间隔0.050秒，低于0.075秒上限，编码速度1.752x；部署初筛不通过，相对输入源流节省-129.72%，决定为原码流直通。
- 任务8使用同一60秒目录执行正式 `compare`：第一次运行复用工程基线8/8和优化组合8/8候选，并重新完成连续性验证；v0.9.0下第二次完全相同请求命中整次实验缓存，0.188秒完成，不再进入搜索和解码。状态为completed、attempt=1，结果位于 `results/monitor-60s-balanced-rate-control/comparison.json`。

### 真实监控素材 AQ 12 秒回归

- 2026-07-22 使用 `F:\work\课题\监控素材.mp4` 前12秒完成综合模式 AQ 研究。编码日志确认实际参数分别为默认 AQ2/1.0/qg32、暗场 AQ3/1.0/qg16、边缘 AQ4/1.0/qg16，CU-tree均开启，aq-motion均关闭。
- 默认 AQ2 最高合格点为 CRF 25.5，VMAF 95.202、P5 93.700、SSIM 0.993952、平均码率 1.148 Mbit/s、速度 1.853x。
- 暗场 AQ3 最高合格点同为 CRF 25.5，VMAF 95.124、P5 94.112、SSIM 0.994478、平均码率 1.232 Mbit/s。等画质配对 `|ΔVMAF|=0.063`，平均码率变化 -7.51%，即码率增加，拒绝采用。
- 边缘 AQ4 最高合格点降至 CRF 22.5，VMAF 95.091、P5 93.947、SSIM 0.994480、平均码率 1.235 Mbit/s。等画质配对 `|ΔVMAF|=0.036`，平均码率变化 -12.20%，即码率增加，拒绝采用。
- 本样本结论为保持默认 AQ2。第二次执行24个AQ候选全部命中缓存。该结论只说明AQ3/AQ4在本片段和全局指标约束下没有节码率收益，不否定它们在真实暗场、文字边缘或空间ROI指标下可能有价值。

### 真实监控素材静态 ROI 12 秒回归

- 2026-07-22 使用 `F:\work\课题\监控素材.mp4` 前 12 秒完成综合模式静态 ROI 研究。无 ROI AQ2 对照最高合格点为 CRF 25.5，ROI 最高合格点为 CRF 26.5。
- 全局等画质配对为 CRF 25.5 对 26.5：无 ROI VMAF 95.202、P5 93.700、SSIM 0.993952、平均 1.148 Mbit/s；ROI VMAF 95.193、P5 94.203、SSIM 0.992720、平均 1.202 Mbit/s、速度 1.594x，`|delta VMAF|=0.009`。
- 中央玻璃门、室外主通道、右侧停车/门口的局部 VMAF/P5 均未下降，时间戳 SSIM 也未下降，说明重点区域得到了质量保护。
- ROI 候选的平均视频包码率反而增加 4.70%，因此当前综合 QP 策略没有正向码率收益，程序正确回退无 ROI AQ2 对照，未生成部署文件。按验收规则不继续执行 60 秒 ROI 回归。
- 第二次执行时无 ROI/ROI 各 8 个候选和 8 条局部指标全部命中缓存。结果位于 `results/roi-monitor-12s-balanced`。

### 真实监控素材 ROI 保护降噪回归

- 2026-07-23 使用 `F:\work\课题\监控素材.mp4` 前 12 秒完成综合模式 ROI 保护降噪研究。无降噪 AQ2 与降噪候选均配对在 CRF 25.0：码率由 1.196 降至 1.183 Mbit/s，降低 1.12%；`|delta VMAF|=0.069`，全局 VMAF/P5/SSIM、三个 critical 区域、时间戳和 1.61x 编码速度全部通过。
- 因 12 秒实验取得正收益，继续完成完整 60 秒、1200 帧回归。无降噪和降噪最高合格点均为 CRF 25.5；无降噪为 1.129 Mbit/s、VMAF 95.199、P5 93.713、SSIM 0.993970、速度 2.043x，降噪为 1.116 Mbit/s、VMAF 95.147、P5 94.116、SSIM 0.993067、速度 1.619x。
- 60 秒配对 `|delta VMAF|=0.052`，平均视频包码率降低 1.13%。中央玻璃门、室外主通道、右侧停车/门口的 VMAF/P5 均未下降，时间戳 SSIM 也未下降，四个重点区域全部通过，因此研究流程选中 ROI 保护降噪。
- 第二次 60 秒执行时，无降噪 8 个候选、降噪 8 个候选和 8 条局部指标全部命中缓存。完整结果位于 `results/denoise-monitor-60s-balanced`。
- 该 1.13% 仅是相对同条件 H.265 无降噪对照的算法收益；降噪输出仍高于约 0.491 Mbit/s 的输入 H.264 源流，不生成部署文件，也不能据此宣称摄像头侧已经节码率。

## 当前关键决策

1. 当前正式入口 `multi-encode` 主输出对象为 x265 原生默认、通用无 ROI、预算中性 ROI、ROI + 降噪实验项四路 H.265 结果；V1.3 保守/综合/激进、V1.2 激进+、激进++、激进+++和早期“工程基线 H.265 vs 帧间优化组合 H.265”的双方案比较仅作为历史研究命令/内部模式保留。
2. 两种 H.265 方案必须从同一参考帧序列出发，并分别寻找满足同一画质门槛的编码点。
3. 当前 V1.4 先落地预算中性 ROI 决策，不做单参数消融；ROI QP、降噪、GOP 和 lookahead 各自贡献顺延到后续消融。
4. 当前四路正式报告只记录分辨率、平均视频包码率、CRF、VMAF/P5/SSIM、编码速度、相对默认节省、相对通用无 ROI 节省、预算中性和 ROI 局部保持/改善，不评选胜出方案，也不生成部署结论。
5. 第一版先实现算法核心和电脑端标定工具；摄像头控制适配层、网关运行层只保留接口边界，后续实机阶段再实现。
6. x265 中 `keyint` 控制最大帧内间隔，`scenecut` 允许在场景切换时提前插入帧内帧，周期帧内刷新可将刷新分摊到多帧；当前 V1.4 通用无 ROI、预算中性 ROI 和 ROI + 降噪实验项均使用 10 秒最大 GOP，动态 GOP 和周期帧内刷新待后续单独验证。
7. 后续每次交付必须分开列出“本次完成”、“尚未完成”、“验证结果”和“下一项”，不得把阶段任务完成表述为整个课题已完成。
8. 当前正式 Web/CLI 操作提供默认基准、通用无 ROI、预算中性 ROI、ROI + 降噪实验项。模式定义以 `hevc_lab/core/configs.py` 为唯一代码来源；后续 CRF 搜索、CLI、报告、网页和部署逻辑必须复用，不能各自维护魔法数。
9. 当前四路正式输出不再做“肉眼无损近似配对”，也不再使用 V1.1 目标节省区间选择；三个 V1.4 非默认策略均选择满足本档绝对画质门槛的最高合格 CRF。旧配对规则仅适用于历史 `pair-crf`、`roi-study`、`denoise-study` 等研究命令。
10. ROI 候选平均视频包码率超过通用无 ROI 预算、或任一 critical/evidence 重点区域局部 VMAF/P5/SSIM 低于通用无 ROI 方案时，必须标记失败，不发布对应 ROI 输出。
11. 质量驱动码控以自适应 CRF 为质量主体，以VBV作为局部峰值保护；保守/综合/激进初始峰值倍率为2.0/1.5/1.25，缓冲时长为4/3/2秒。画质复核优先于码率下降，失败必须自动放宽或回退。
12. AQ研究以x265 medium已有的AQ2/strength1.0/qg32为明确对照；AQ3研究暗场偏置，AQ4研究边缘信息。三者必须独立搜索和等画质配对。内置AQ不是语义识别，aq-motion不等同于运动区域保护。
13. 静态 ROI 必须绑定摄像头和参考分辨率，无 ROI/ROI 分别搜索 CRF，再以全局和critical/evidence局部画质共同决策。仅保护局部画质但平均码率不降时必须回退无ROI对照；固定ROI不得表述为已检测人员/车辆。
14. 无效噪声抑制复用静态 ROI 但不叠加 ROI QP 偏移：evidence 原图直通、critical 轻度降噪、normal 中等、discard 较强。无降噪/降噪必须独立搜索，任一全局、局部、速度或正码率收益条件失败即回退；当前结果不得外推到雨雪、烟雾、闪烁或摄像头参数控制。
15. 软件侧不再用严格1.0x作为唯一通过线：连续性检查必须验证完整解码、规格、帧数、时长和逐帧PTS；编码速度达到0.97x可判定有限片段近实时连续处理并注明会累积延迟，达到1.0x才标记当前电脑可实时处理。
16. `compare` 的整次实验缓存只在请求哈希相同、状态completed、结果可读且配对候选文件仍存在时短路命中；失败或中断状态必须增加attempt并重走编排，由参考/候选缓存负责避免重复编码和质量计算。

## 常用命令

当前已有命令：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab check-env
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m unittest discover -v
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab experiment --input '.\samples\interframe-sample.mkv' --output '.\results\balanced-run' --mode balanced
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab search-crf --input '.\samples\interframe-sample.mkv' --output '.\results\single-search' --mode balanced --scheme optimized --duration 12
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab pair-crf --input '.\samples\interframe-sample.mkv' --output '.\results\pair-search' --mode balanced --duration 12
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab rate-control --input '.\samples\interframe-sample.mkv' --output '.\results\rate-control' --mode balanced --scheme optimized --duration 12
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab aq-study --input 'F:\work\课题\监控素材.mp4' --output '.\results\aq-monitor-12s-balanced' --mode balanced --scheme optimized --duration 12
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab roi-study --input 'F:\work\课题\监控素材.mp4' --roi-config '.\configs\camera-entrance-roi.json' --output '.\results\roi-monitor-12s-balanced' --mode balanced --scheme optimized --duration 12
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab denoise-study --input 'F:\work\课题\监控素材.mp4' --roi-config '.\configs\camera-entrance-roi.json' --output '.\results\denoise-monitor-12s-balanced' --mode balanced --scheme optimized --duration 12
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m hevc_lab multi-encode --input 'F:\work\课题\监控素材.mp4' --roi-config '.\configs\camera-entrance-roi.json' --output '.\results\monitor-v1_4'
```

`compare` 和 `web` 命令均已实现；当前正式四路输出入口为 `multi-encode`，本地网页入口为 `web`，本地页面文件位于 `apps/web`。

## v1.4.0 预算中性 ROI 四路输出

- V1.4 正式入口输出 `default_x265.mp4`、`generic_no_roi.mp4`、`budget_neutral_roi.mp4` 和 `roi_denoise_experimental.mp4`。
- 默认为 x265 原生默认编码；通用无 ROI 为当前 ROI 预算基准；预算中性 ROI 只叠加静态 ROI QP；ROI + 降噪实验项叠加静态 ROI QP 和 ROI 保护分区降噪。
- 三个非默认策略统一使用 VMAF≥83、P5≥80、SSIM≥0.950，CRF 搜索范围均为18～38。
- ROI 两路必须满足平均视频包码率≤通用无 ROI 方案，并且 critical/evidence 重点区域局部 VMAF、P5、SSIM 不低于通用无 ROI 方案；失败时不发布对应 ROI 输出。
- `research_manifest.json` 记录相对默认节省、相对通用无 ROI 节省、预算中性结果、ROI 重点区域是否保持/改善和失败原因。
- 2026-07-24 使用 `F:\work\课题\监控素材.mp4` 完成60秒、1920×1080、20fps 四路 V1.4 回归，结果目录为 `results/monitor-v1_4`：
  - x265 原生默认：0.568393 Mbit/s。
  - 通用无 ROI 方案：CRF 36.5，0.206501 Mbit/s，相对默认节省 63.67%；VMAF 85.438、P5 80.577、SSIM 0.975043、编码速度 2.071x。
  - 预算中性 ROI 方案：CRF 37.5，全局 VMAF 85.612、P5 80.406、SSIM 0.973342、编码速度 2.297x，但尝试平均视频包码率 0.274063 Mbit/s，高于通用无 ROI 预算 0.206501 Mbit/s，相对通用节省 -32.72%，因此预算失败，未生成 `budget_neutral_roi.mp4`。
  - ROI + 降噪实验项：CRF 38.0，全局 VMAF 84.908、P5 81.829、SSIM 0.971679、编码速度 1.963x，但尝试平均视频包码率 0.255483 Mbit/s，高于通用无 ROI 预算 0.206501 Mbit/s，相对通用节省 -23.72%，因此预算失败，未生成 `roi_denoise_experimental.mp4`。
- 本次 V1.4 结论：ROI 两路全局质量均达到最低门槛，但都不能在通用无 ROI 预算内保护重点区域，程序正确标记 ROI 失败并只发布 `default_x265.mp4` 与 `generic_no_roi.mp4`；这不是部署结论，也不代表摄像头硬件实机验证成功。

## v1.3.0 默认 / 保守 / 综合 / 激进四路重构

- V1.3 正式入口只输出 `default_x265.mp4`、`composite_conservative.mp4`、`composite_balanced.mp4`、`composite_aggressive.mp4`。
- 默认为 x265 原生默认编码；保守为 V1.0 原激进帧间结构的通用 `medium` 版且无 ROI/降噪；综合为 V1.0 原激进方案；激进为 V1.2 激进+方案。
- 三个非默认策略统一使用 VMAF≥83、P5≥80、SSIM≥0.950；保守和综合 CRF 搜索范围为18～38，激进为18～42。
- `research_manifest.json` 记录 `public_mode`、`source_mode`、`strategy_generation`、`region_processing_enabled`、`roi_enabled`、`denoise_enabled`、`effective_preset` 和 `crf_search_max`。
- 2026-07-24 使用 `F:\work\课题\监控素材.mp4` 完成60秒、1920×1080、20fps 四路 V1.3 回归，结果目录为 `results/monitor-v1_3`：
  - x265 原生默认：0.568393 Mbit/s。
  - 保守通用策略：CRF 36.5，0.206501 Mbit/s，相对默认节省 63.67%；VMAF 85.438、P5 80.577、SSIM 0.975043、编码速度 1.204x。
  - 综合策略：CRF 38.0，0.294857 Mbit/s，相对默认节省 48.12%；VMAF 87.040、P5 86.241、SSIM 0.973285、编码速度 0.675x。
  - 激进策略：CRF 39.0，0.237818 Mbit/s，相对默认节省 58.16%；VMAF 83.891、P5 83.043、SSIM 0.967868、编码速度 0.492x。
- 四个最终 MP4 均完成 FFmpeg 全文件解码检查，退出码均为0。第二次相同运行确认默认、短片搜索候选和完整候选缓存命中。
- `research_manifest.json` 确认保守通用策略 `region_processing_enabled=false`、`roi_enabled=false`、`denoise_enabled=false`、`effective_preset=medium`；综合映射 `v1.0_aggressive`；激进映射 `v1.2_aggressive_plus`。
- 结果边界：V1.3 只记录软件 libx265 数据，不评选胜出方案，不作为摄像头硬件部署结论；保守通用策略在该素材上最低码率，主要因为去掉区域处理后能以 CRF 36.5 通过统一最低画质门槛。

## v1.1.0 目标节省区间迭代与真实素材结果

- V1.1 只修改保守和均衡两档；激进模式保持 V1.0 的 `slow` preset、VMAF 83/P5 80/SSIM 0.950、ref=6、bframes=8、lookahead=90、10秒/2秒 GOP 和最高合格 CRF 选择逻辑。
- 保守模式改为 VMAF 90/P5 88/SSIM 0.980、ref=4、bframes=5、lookahead=45、8秒/2秒 GOP，目标相对 x265 原生默认节省 10%～15%。
- 均衡模式改为 VMAF 90/P5 88/SSIM 0.980、ref=5、bframes=6、lookahead=60、10秒/2秒 GOP，目标相对 x265 原生默认节省 20%～30%。
- 保守 ROI QP 改为 critical=-1、evidence=-2、normal=+4、discard=+6；均衡改为 critical=-2、evidence=-2、normal=+5、discard=+8；激进不变。
- 保守 ROI 保护降噪改为 critical=`0.3:0.225:0.45:0.35`、normal=`1.2:0.9:2.0:1.5`、discard=`2.0:1.5:3.4:2.5`、evidence原图直通；均衡改为 critical=`0.4:0.3:0.6:0.45`、normal=`1.6:1.2:2.8:2.1`、discard=`2.4:1.8:4.0:3.0`、evidence原图直通；激进不变。
- CRF 搜索改为保守/均衡“画质通过 + 目标节省区间优先”：目标区间内选最低码率候选；没有目标区间候选但有画质通过候选时仍生成视频，并标记 `target_saving_met=false` 和 `saving_target_status`。
- 2026-07-23 使用 `F:\work\课题\监控素材.mp4` 完成60秒、1920×1080、20fps、1200帧四路 V1.1 回归，结果目录为 `results/monitor-four-strategies-v1_1`：
  - x265 原生默认：0.568393 Mbit/s。
  - 保守综合策略：CRF 30.0，0.557291 Mbit/s，相对默认节省 1.95%；VMAF 91.981、P5 88.468、SSIM 0.985393；低于 10%～15% 目标区间，`target_saving_met=false`、`saving_target_status=below_target`。
  - 均衡综合策略：CRF 31.0，0.530009 Mbit/s，相对默认节省 6.75%；VMAF 91.300、P5 88.292、SSIM 0.983565；低于 20%～30% 目标区间，`target_saving_met=false`、`saving_target_status=below_target`。
  - 激进综合策略：CRF 38.0，0.294857 Mbit/s，相对默认节省 48.12%；VMAF 87.040、P5 86.241、SSIM 0.973285；目标节省区间不适用。
- 四个最终 MP4 均完成 FFmpeg 全文件解码检查，退出码均为0。
- 第二次相同运行确认缓存命中：默认、保守、均衡、激进四路 `cache_hit=True`；保守短片7个候选、完整3个候选命中，均衡短片8个候选、完整2个候选命中，激进短片4个候选、完整1个候选命中。
- 结论边界：V1.1 程序逻辑完成且能如实报告未命中目标；当前保守/均衡参数在该素材上没有达到用户期望的 10%～15% 和 20%～30% 节省，需要后续继续调参或引入更强的降噪/ROI/码控策略。

## v1.2.0 激进三档扩展与真实素材结果

- V1.2 当前正式入口只输出 x265 原生默认、激进+、激进++、激进+++四路；暂时不输出保守、均衡和原激进档。
- 三个新激进档全部使用 `slow` preset，画质门槛统一为 VMAF≥83、P5≥80、SSIM≥0.950；CRF 搜索上限分别为 42、45、48；20fps 下最大 GOP 分别为 240、300、400 帧，min-keyint 均为40帧。
- ROI QP 分别为：激进+ critical=-4/evidence=-3/normal=+7/discard=+12；激进++ 为 -4/-3/+9/+16；激进+++ 为 -4/-3/+11/+20。
- ROI 保护降噪分别增强到：激进+ critical=`0.7:0.525:1.2:0.9`、normal=`2.2:1.65:3.6:2.7`、discard=`3.2:2.4:5.0:3.75`；激进++ critical=`0.8:0.6:1.4:1.05`、normal=`2.6:1.95:4.4:3.3`、discard=`3.8:2.85:6.0:4.5`；激进+++ critical=`0.9:0.675:1.6:1.2`、normal=`3.0:2.25:5.2:3.9`、discard=`4.4:3.3:7.0:5.25`；evidence 均原图直通。
- 2026-07-24 使用 `F:\work\课题\监控素材.mp4` 完成60秒、1920×1080、20fps 四路 V1.2 回归，结果目录为 `results/monitor-aggressive-plus-v1_2`：
  - x265 原生默认：0.568393 Mbit/s。
  - 激进+综合策略：CRF 39.0，0.237818 Mbit/s，相对默认节省 58.16%；VMAF 83.891、P5 83.043、SSIM 0.967868、编码速度 0.989x。
  - 激进++综合策略：CRF 38.0，0.269104 Mbit/s，相对默认节省 52.66%；VMAF 83.810、P5 83.265、SSIM 0.967848、编码速度 0.863x。
  - 激进+++综合策略：CRF 37.0，0.312427 Mbit/s，相对默认节省 45.03%；VMAF 83.516、P5 82.978、SSIM 0.967412、编码速度 1.001x。
- 四个最终 MP4 均完成 FFmpeg 全文件解码检查，退出码均为0。第二次相同运行确认默认、短片搜索候选和完整候选缓存命中。
- 结果边界：V1.2 显示“更激进”并不必然更省码率；激进++和激进+++因强降噪/强背景QP/长GOP使画质边界提前下降，最终必须降低 CRF 通过门槛，平均码率反而高于激进+。本结果仍只记录数据，不评选胜出方案，也不是摄像头实机部署结论。

## v1.0.0 当前方向与真实素材结果

本节覆盖上文仍保留的旧版双方案比较决策。

- 当前正式入口为 `multi-encode`，独立生成 x265 原生默认、保守综合、均衡综合、激进综合四路视频。
- 三档综合策略共同组合 AQ2、模式帧间参数、ROI 保护分区降噪和静态 ROI QP；不启用 VBV。
- 三档分别使用前12秒搜索自己的最高合格 CRF，再在完整输入上复核；不做 CRF 配对、VMAF/SSIM 差值配对或胜出方案选择。
- 最终只展示分辨率、平均视频包码率和相对 x265 默认方案的码率节省百分比；负数按原值保留。
- 2026-07-23 使用 `F:\work\课题\监控素材.mp4` 完成60秒、1920×1080、20fps、1200帧四路实验：
  - x265原生默认：0.568393 Mbit/s。
  - 保守综合策略：CRF 26.5，1.384950 Mbit/s，相对默认节省 -143.66%；VMAF 95.240、P5 93.185、SSIM 0.993106。
  - 均衡综合策略：CRF 32.0，0.653719 Mbit/s，相对默认节省 -15.01%；VMAF 91.837、P5 88.340、SSIM 0.985548。
  - 激进综合策略：CRF 38.0，0.294857 Mbit/s，相对默认节省 48.12%；VMAF 87.040、P5 86.241、SSIM 0.973285。
- 四路输出均为1920×1080、20fps、1200帧、60秒，FFmpeg完整解码退出码均为0。
- 第二次相同运行耗时44.346秒；默认输出命中缓存，保守/均衡/激进短片搜索分别8/8、8/8、4/4命中，三档完整候选均1/1命中。
- v1.0.0 单元测试增加到100项并全部通过；`addroi + hqdn3d + libx265`真实1秒组合烟雾编码通过。

当前命令：

```powershell
python -m hevc_lab multi-encode `
  --input 'F:\work\课题\监控素材.mp4' `
  --roi-config '.\configs\camera-entrance-roi.json' `
  --output '.\results\monitor-four-strategies'
```

### 2026-07-23 本地 Web 第一版

- 项目新增本地 Web 第一版，入口为 `python -m hevc_lab web --host 127.0.0.1 --port 8000`，服务只允许监听 `127.0.0.1`。
- 代码按轻量 monorepo 方式组织：现有 Python 算法和 API 保留在 `hevc_lab/`，原生前端放在 `apps/web/`，不引入 React/Vue 构建系统。
- FastAPI API 支持 `POST /api/jobs`、`GET /api/jobs/{job_id}`、`GET /api/jobs/{job_id}/results`、`GET /api/jobs/{job_id}/files/{filename}` 和 `GET /api/jobs/{job_id}/previews/{strategy_id}`。
- RTSP 实时预览 API 支持 `POST /api/streams`、`GET /api/streams/{stream_id}`、`GET /api/streams/{stream_id}/hls/{filename}` 和 `DELETE /api/streams/{stream_id}`。该 API 启动 FFmpeg 将 RTSP 转为 HLS 临时分片，状态中只回显脱敏 RTSP 地址，停止后清理 `work/live_streams/<stream_id>/`。
- 每个网页任务使用独立目录 `work/web_jobs/<job_id>/`，后端直接调用 `run_multi_encode()`，并通过 `progress_callback` 展示 queued、preparing_reference、encoding_default、searching/validating 三档、completed 和 failed 等状态。
- 第一版线程池只允许一个编码任务运行，其余任务排队，避免多路 x265 同时压满 CPU。
- 服务端为四个 H.265 输出额外生成 H.264 浏览器预览；预览只用于观看，不参与 H.265 码率、画质或节省指标。
- 前端采用重叠式视频对比滑块：底层默认 x265 视频完整显示，上层选中的保守/均衡/激进综合策略视频通过 `clip-path` 裁剪显示；拖动中间分割线改变上层可见宽度。
- 两个预览视频同步播放、暂停、进度和倍速，只保留一路声音，并定期校准播放时间偏差。页面保留负节省值显示，不评选最佳方案，不输出部署结论。

### 2026-07-27 本地 RTSP 实时双画面对比

- 当前本地 Web 首页改为服务 `apps/demo_live/`，该目录复用 `apps/demo/` 的视觉和重叠式视频对比形态，但依赖 FastAPI 和本地 FFmpeg，不用于 Cloudflare Pages。
- `apps/demo/` 保持纯静态展示版，不调用 `/api/*`，继续作为 Cloudflare Pages 发布目录。
- RTSP 实时预览从单路 HLS 扩展为同一 stream session 下的两路摄像头输入：`source` 是原生 H.264，`conservative` 是摄像头侧按既有保守策略输出的 H.265/HEVC。保守策略参数仍来自摄像头侧和既有项目定义，本地实时预览模块不重写 CRF、GOP、ROI、降噪、AQ、preset 或 x265 参数。
- H.265 保守路为了浏览器预览会在本机转为 H.264 HLS；该预览转码不参与带宽节省计算。页面展示的带宽节省只按两路摄像头输入码流实时估算，仍不输出 VMAF、CRF 搜索或部署结论。
