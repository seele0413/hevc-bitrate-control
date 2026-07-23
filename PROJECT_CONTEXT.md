# H.265 课题共享上下文

## 项目位置

- 当前实体目录：`F:\work\课题\h265-mvp`
- 兼容路径：`C:\Users\31969\work\课题\h265-mvp`，该路径指向 F 盘实体目录。
- Python 3.9：`C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe`
- 项目内工具：`.tools\ffmpeg\bin\ffmpeg.exe`、`.tools\ffmpeg\bin\ffprobe.exe` 和 `.tools\vmaf`。

## 当前课题目标

课题研究固定监控画面的 H.265 低码率编码。当前阶段不直接做实时网关转码，而是先用可重复实验回答：

> 对同一组参考画面，优化参考帧、B 帧、前向分析和 GOP 等帧间预测参数后，是否能在基本相同的客观画质下，比工程基线 H.265 使用更低码率？

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
- 已固化 `libx265 + medium + Main 8-bit + yuv420p` 统一编码条件，工程基线为 2 秒最大 GOP，保守/综合/激进优化组合分别为 2/4/6 秒最大 GOP。
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
- 现有 84 项单元测试通过，libx265/libvmaf/addroi/hqdn3d 环境检查通过。
- 已用 12 秒合成样本完成两套正式参数的端到端回归：300 帧、首帧 PTS=0，优化组合相对工程基线节省 2.32%。

当前原型的关键不足：

- 两方案搜索、配对、视频包字节口径、三种正式可行性结论和最终 `compare` 编排已经完成，但长期跨片段平均码率反馈和任务9的最终报告格式尚未实现。
- 当前优化组合同时改变了多个帧间参数，只能判断组合整体是否有效，不能作为严格的单参数消融结论。
- 当前只实现固定坐标 ROI，没有动态人员/车辆检测、跟踪、语义 ROI 和隐私遮挡。
- 当前降噪只覆盖随机空域/时域噪声；雨雪、烟雾、光线闪烁、过度锐化振铃和摄像头增益/曝光控制尚未实现。
- 当前没有本地上传和两路结果同步对比页面。
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

1. 程序主对比对象为“工程基线 H.265”和“帧间优化组合 H.265”，不是直接把源视频和某个随意压低码率的结果对比。
2. 两种 H.265 方案必须从同一参考帧序列出发，并分别寻找满足同一画质门槛的编码点。
3. 第一版主要证明组合方案整体是否有效；单个参考帧、B 帧、lookahead 和 GOP 的贡献放到 V1.1 消融实验。
4. 报告必须同时给出算法可行性和部署可行性，二者不得混淆。
5. 第一版先实现算法核心和电脑端标定工具；摄像头控制适配层、网关运行层只保留接口边界，后续实机阶段再实现。
6. x265 中 `keyint` 控制最大帧内间隔，`scenecut` 允许在场景切换时提前插入帧内帧，周期帧内刷新可将刷新分摊到多帧；当前 V1.0 只做保守/综合/激进三档 2/4/6 秒静态 GOP 对比，动态 GOP 和周期帧内刷新待组合结论成立后再单独验证。
7. 后续每次交付必须分开列出“本次完成”、“尚未完成”、“验证结果”和“下一项”，不得把阶段任务完成表述为整个课题已完成。
8. 用户操作统一提供保守、综合、激进三种模式，默认综合模式。模式定义以 `hevc_lab/core/configs.py` 为唯一代码来源；后续 CRF 搜索、CLI、报告、网页和部署逻辑必须复用，不能各自维护魔法数。
9. “肉眼无损近似配对”采用两层约束：两路先分别通过所选模式绝对画质门槛，再要求 VMAF 平均值差绝对值不超过 1.0；低 CRF 高画质锚点不进入边界配对池。
10. 质量驱动码控以自适应 CRF 为质量主体，以VBV作为局部峰值保护；保守/综合/激进初始峰值倍率为2.0/1.5/1.25，缓冲时长为4/3/2秒。画质复核优先于码率下降，失败必须自动放宽或回退。
11. AQ研究以x265 medium已有的AQ2/strength1.0/qg32为明确对照；AQ3研究暗场偏置，AQ4研究边缘信息。三者必须独立搜索和等画质配对。内置AQ不是语义识别，aq-motion不等同于运动区域保护。
12. 静态 ROI 必须绑定摄像头和参考分辨率，无 ROI/ROI 分别搜索 CRF，再以全局和critical/evidence局部画质共同决策。仅保护局部画质但平均码率不降时必须回退无ROI对照；固定ROI不得表述为已检测人员/车辆。
13. 无效噪声抑制复用静态 ROI 但不叠加 ROI QP 偏移：evidence 原图直通、critical 轻度降噪、normal 中等、discard 较强。无降噪/降噪必须独立搜索，任一全局、局部、速度或正码率收益条件失败即回退；当前结果不得外推到雨雪、烟雾、闪烁或摄像头参数控制。
14. 软件侧不再用严格1.0x作为唯一通过线：连续性检查必须验证完整解码、规格、帧数、时长和逐帧PTS；编码速度达到0.97x可判定有限片段近实时连续处理并注明会累积延迟，达到1.0x才标记当前电脑可实时处理。
15. `compare` 的整次实验缓存只在请求哈希相同、状态completed、结果可读且配对候选文件仍存在时短路命中；失败或中断状态必须增加attempt并重走编排，由参考/候选缓存负责避免重复编码和质量计算。

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
```

`compare` 已实现；`web` 命令仍属于 `DESIGN_V1.md` 规划接口，在任务完成前不得假定已经存在。

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
- 每个网页任务使用独立目录 `work/web_jobs/<job_id>/`，后端直接调用 `run_multi_encode()`，并通过 `progress_callback` 展示 queued、preparing_reference、encoding_default、searching/validating 三档、completed 和 failed 等状态。
- 第一版线程池只允许一个编码任务运行，其余任务排队，避免多路 x265 同时压满 CPU。
- 服务端为四个 H.265 输出额外生成 H.264 浏览器预览；预览只用于观看，不参与 H.265 码率、画质或节省指标。
- 前端采用重叠式视频对比滑块：底层默认 x265 视频完整显示，上层选中的保守/均衡/激进综合策略视频通过 `clip-path` 裁剪显示；拖动中间分割线改变上层可见宽度。
- 两个预览视频同步播放、暂停、进度和倍速，只保留一路声音，并定期校准播放时间偏差。页面保留负节省值显示，不评选最佳方案，不输出部署结论。
