# H.265 默认编码与三档综合策略研究工具 V1.0

> 当前实现版本：v0.11.0  
> 本节是 `DESIGN_V1.md` 的当前唯一有效方案。本文后续仍保留的“双方案等画质比较、部署结论”等内容只作为历史实现说明，不得覆盖本节。

## 0. v0.11.0 当前唯一方案

### 0.1 研究目标

程序对同一个输入独立生成四个 H.265 视频：

1. `default_x265.mp4`：只指定 `libx265`，不传自定义 CRF、preset、AQ、ROI、降噪或帧间参数。
2. `composite_conservative.mp4`：保守综合策略。
3. `composite_balanced.mp4`：均衡综合策略。
4. `composite_aggressive.mp4`：激进综合策略。

四路结果不执行 CRF 配对、不计算 VMAF/SSIM 差值、不评选胜出方案，也不输出部署结论。三档综合策略分别搜索自己的最高合格 CRF；最终只把实际输出分辨率、平均视频包码率及相对默认方案的码率节省百分比展示给用户。

### 0.2 三档综合策略

| 模式 | preset | VMAF / P5 / SSIM | ref / B帧 / lookahead | 最大 / 最小 GOP |
|---|---|---|---|---|
| `conservative` | `medium` | 95 / 93 / 0.990 | 4 / 4 / 30 | 2秒 / 1秒 |
| `balanced` | `medium` | 90 / 88 / 0.980 | 5 / 6 / 60 | 4秒 / 2秒 |
| `aggressive` | `slow` | 83 / 80 / 0.950 | 6 / 8 / 90 | 10秒 / 2秒 |

三档共同启用：

- `b-adapt=2`、`b-pyramid=1`、`scenecut=40`。
- AQ2、`aq-strength=1.0`、`qg-size=32`、`aq-motion=0`。
- 当前模式的静态 ROI QP 偏移。
- 当前模式的 ROI 保护分区降噪。
- 不启用 VBV，不缩放分辨率，不改变帧率。

组合滤镜顺序固定为：

```text
ROI 分区降噪合成 → addroi 量化区域信息 → libx265
```

### 0.3 独立 CRF 搜索

- 默认 x265 方案不搜索 CRF，直接编码完整输入。
- 三档综合策略分别使用输入前 12 秒，在 CRF 18～38、步长 0.5 中搜索满足本档 VMAF、P5、SSIM 绝对门槛的最高 CRF。
- 使用各档选中的 CRF 编码完整输入。
- 完整输出未达到本档门槛时，只对该档按 0.5 逐级降低 CRF 并重编码，直到通过或到达 CRF 18。
- 某档在 CRF 18 仍不合格时记录失败，不生成虚假的最终数据。
- 编码速度只记录，不参与三档输出的淘汰。

### 0.4 码率口径与结果

平均码率只使用 FFprobe 汇总视频包字节计算，不混入音频与容器开销：

```text
节省百分比 =
(默认方案平均视频包码率 - 综合方案平均视频包码率)
÷ 默认方案平均视频包码率
× 100%
```

正数表示节省，负数表示码率增加，负数不得截断或隐藏。JSON 保留完整精度，CSV 和 Markdown 保留两位小数。

公开输出：

- `final_metrics.csv`
- `final_summary.md`
- 四个最终视频

内部研究记录：

- `research_manifest.json`
- 三档 CRF 搜索点、画质指标、编码速度、完整编码参数和缓存信息

正式接口：

```powershell
python -m hevc_lab multi-encode `
  --input 'F:\work\课题\监控素材.mp4' `
  --roi-config '.\configs\camera-entrance-roi.json' `
  --output '.\results\monitor-four-strategies'
```

### 0.5 本地 Web 界面

v0.11.0 Web 界面只服务于四路正式输出的本地展示和下载，不恢复旧版 RTSP、MediaMTX、动态码率或双方案部署结论页面。

启动入口：

```powershell
python -m hevc_lab web --host 127.0.0.1 --port 8000
```

约束：

- 使用 FastAPI + Uvicorn，仅监听 `127.0.0.1`。
- 前端放在 `apps/web/`，使用原生 HTML、CSS 和 JavaScript，不引入 React/Vue 构建系统。
- 后端直接调用 `hevc_lab.multi_encode.run_multi_encode()`，不得使用 `shell=True` 拼接命令。
- 每个任务使用独立目录 `work/web_jobs/<job_id>/`。
- 第一版线程池只允许一个编码任务运行，后续任务保持 `queued` 排队，避免 CPU 过载。
- 上传只接受非空 MP4/MKV 文件。
- 服务端额外生成 H.264 浏览器预览；预览只用于页面观看，不参与 H.265 码率、画质或节省指标。
- 页面以 `default_x265` 作为底层完整视频，以当前选中的保守、均衡或激进综合策略作为上层裁剪视频，通过中间滑块改变上层可见宽度。
- 两个预览视频必须同步播放、暂停、进度和倍速；只保留一路声音，另一路静音；定期检查播放时间偏差并重新对齐。
- 页面展示四路分辨率、平均视频包码率和相对默认方案节省百分比；负节省值必须原样显示为负数。
- 页面提供四个 H.265 输出文件下载，不评选最佳方案，不输出部署结论。

API：

```text
POST /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/results
GET  /api/jobs/{job_id}/files/{filename}
GET  /api/jobs/{job_id}/previews/{strategy_id}
```

任务状态至少包括：

```text
queued
preparing_reference
encoding_default
searching_conservative
validating_conservative
searching_balanced
validating_balanced
searching_aggressive
validating_aggressive
completed
failed
```

---

## 以下内容为 v0.10.0 及更早历史实现说明

## 1. 第一版要回答的问题

第一版只回答一个可验证问题：

> 对同一组参考画面，帧间优化组合能否在基本相同的画质下，比工程基线 H.265 使用更低的视频码率？

第一版不证明摄像头硬件编码器已经支持这些参数，也不负责 RTSP、网关转发、动态码率和摄像头控制。

## 2. 第一性原理

编码优化目标为：

```text
最小化：视频码率 R(参数组合, CRF)

约束：
VMAF 平均值、VMAF P5、SSIM ≥ 用户所选模式的门槛
分辨率、帧率、帧数和时长保持一致
```

程序不得把“降低目标码率”本身当成算法。正确方法是让工程基线和优化组合分别搜索自己的合格 CRF，再在同一模式的相同画质门槛下比较码率。默认的“综合模式”门槛仍为 VMAF 平均值 95、VMAF P5 93、SSIM 0.99。

## 3. V1.0 范围

### 必须实现

- 本地网页选择并上传 MP4、MKV 等监控视频。
- 探测输入编码、分辨率、帧率、帧数、时长和视频流码率。
- 从输入中截取统一的代表性片段，默认 15 秒。
- 生成两路完全相同来源的参考帧输入。
- 工程基线 H.265 独立搜索合格 CRF。
- 帧间优化组合 H.265 独立搜索合格 CRF。
- 计算 VMAF 平均值、VMAF P5、SSIM、编码速度和视频流码率。
- 生成两种 H.265 结果及浏览器观看副本。
- 同步展示、播放和对比两种方案。
- 分别给出算法可行性、软件画面连续性和部署可行性结论。
- 缓存候选视频和指标，程序中断后可以继续。

### 暂不实现

- 真实 RTSP 拉流。
- 摄像头参数读取或下发。
- MediaMTX 或网关运行层。
- 运行过程中动态切换参数。
- 动态/语义 ROI、目标检测与跟踪、长期背景模型和摄像头增益/曝光控制。
- 使用高 QP 代替隐私遮挡；隐私区必须另行模糊或覆盖。
- 多摄像头并发。
- 单参数消融和完整 BD-Rate；它们分别进入 V1.1 和 V1.2。

## 4. 四部分架构边界

项目长期拆分为四部分，但 V1.0 只完整实现其中两部分：

1. **算法核心**：参数模型、质量搜索、指标计算、结论判断；V1.0 实现。
2. **摄像头控制适配层**：未来把算法参数映射到厂商 SDK/ONVIF；V1.0 只定义接口边界。
3. **网关运行层**：未来负责实时运行、监控和回退；V1.0 不实现。
4. **电脑端标定工具**：文件上传、离线实验、网页对比和报告；V1.0 实现。

建议代码结构：

```text
hevc_lab/
├─ core/                 # 纯算法、搜索和结论判断
├─ encoders/             # libx265命令生成与执行
├─ metrics/              # VMAF、SSIM和视频流码率
├─ adapters/             # 文件输入；未来扩展camera接口
├─ reports/              # JSON、CSV、Markdown
├─ web/                  # 本地标定页面和任务状态
└─ cli.py
```

## 5. 输入参考处理

用户上传的视频是“输入参考视频”，不称为传感器原始视频。

处理步骤：

1. 使用 FFprobe 检查视频流和时间信息。
2. 默认截取 15 秒代表性片段，允许用户设置开始时间和时长。
3. 将时间戳归零，统一为源分辨率、源帧率和 `yuv420p`。
4. 缓存为无损 FFV1 MKV，作为两种 H.265 方案的共同参考。
5. 保存参考帧数量和逐帧时间戳摘要，验证两路编码输入完全一致。

如果磁盘空间不足，允许使用确定性解码管道直接供给两路编码，但报告必须记录实现方式。

## 6. 工程基线与三种帕累托模式

### 6.1 工程基线 H.265

```text
encoder      = libx265
preset       = 所选模式的正式 preset
profile      = Main 8-bit
pixel format = yuv420p
ref          = 3
bframes      = 4
b-adapt      = 2
rc-lookahead = 20
keyint       = 2秒对应帧数
min-keyint   = 1秒对应帧数
scenecut     = 40
cutree       = 1
weightp      = 1
```

该方案统一称为“工程基线 H.265”，不得泛称为所有普通 H.265。

### 6.2 用户可选的帧间优化模式

程序提供三种帕累托权衡模式。每种模式都使用同一模式、同一 preset 下的 6.1 工程基线作算法对照；模式可以改变优化组合、画质门槛、部署收益门槛和正式 preset，但同一次实验的工程基线与优化组合不得使用不同 preset。profile、像素格式和输入参考保持一致。

| 模式 | preset | 速度门槛 | 优先目标 | 固定 CRF 原型默认值 | VMAF / P5 / SSIM 门槛 | 算法节省门槛 | 源流节省门槛 | 优化组合 `ref / bframes / lookahead / 最大GOP / 最小GOP` |
|---|---|---:|---|---:|---|---:|---:|---|
| 保守 `conservative` | `medium` | ≥0.97x | 画质与恢复能力 | 20 | 96 / 94 / 0.995 | 必须为正 | 5% | 4 / 4 / 30 / 2秒 / 1秒 |
| 综合 `balanced` | `medium` | ≥0.97x | 画质、码率、速度、恢复间隔平衡 | 22 | 95 / 93 / 0.990 | 5% | 5% | 5 / 6 / 60 / 4秒 / 2秒 |
| 激进 `aggressive` | `slow` | 不设硬门槛 | 更高节码率潜力，允许离线编码 | 24 | 93 / 90 / 0.985 | 10% | 10% | 6 / 8 / 90 / 6秒 / 3秒 |

三种优化组合共同使用 `b-adapt=2`、`scenecut=40`、`cutree=1`、`weightp=1`。`scenecut` 允许场景切换时提前插入关键帧，但当前仍是三档静态 GOP 策略，不等同于运动量驱动的动态 GOP。

模式定义必须集中保存在算法核心中，命令行、后续 CRF 搜索、报告、网页和部署判断只能引用这一份定义，不得各自复制参数。高级参数可以显式覆盖模式默认值，但报告必须记录模式默认 preset、覆盖值、来源及最终生效值。

保守和综合模式的候选资格要求画质通过且编码速度达到 0.97x；激进模式不使用编码速度淘汰候选，低于 1.0x 时必须明确标记为离线编码。速度达到 1.0x 表示当前电脑可实时处理，达到 1.1x 额外标记为具有工程余量。

V1.0 只证明所选组合整体是否有效。不能根据组合结果宣称其中每一项单独有效，也不能直接比较不同模式的固定 CRF 结果来归因于帧间算法。

## 7. 等画质 CRF 搜索

两种参数分别在 CRF 18～38 范围内搜索，精度为 0.5 CRF。搜索质量门槛取自用户选择的模式；同一次实验中的工程基线和优化组合必须使用同一组门槛。

### 搜索流程

1. 先测试 CRF 18、28、38，建立合格和不合格边界。
2. 使用二分或区间缩小搜索接近门槛的 CRF。
3. 检查最终 CRF 两侧相邻点。
4. 指标明显不满足单调性时补测未覆盖区间。
5. 每个编码结果和指标按“输入哈希＋参数哈希”缓存。
6. 为每种方案选择满足全部画质门槛的最高 CRF。
7. 在两种方案的合格候选中匹配 VMAF 差值最小的一对。

单方案搜索实现约束：

- 搜索决策属于纯算法核心，编码和指标执行通过评估器注入。
- 正常边界只测试锚点、二分点和最终点两侧的 0.5 CRF 相邻点。
- 已测点出现明显非单调证据时，自动补测 CRF 18～38 的完整 41 点网格。
- 候选缓存键必须包含输入 SHA256、统一参考缓存键、完整编码参数、CRF 和 VMAF 模型哈希。
- 单方案搜索结果只表示“满足画质门槛的最高 CRF”，不得提前输出算法或部署成功结论。

### 等画质判断

两路候选必须同时满足：

- VMAF 平均值均不低于 95。
- VMAF P5 均不低于 93。
- SSIM 均不低于 0.99。
- 两路先分别通过所选模式的绝对画质门槛，再要求 VMAF 平均值绝对差不超过 1.0。
- 分辨率、帧率、帧数和时长一致。

无法找到满足这些条件的候选对时，结论必须为“证据不足”，不得宣布优化成功。VMAF 差不超过 1.0 只作为本项目的“肉眼无损近似”配对标准，不能取代两路各自的 VMAF、VMAF P5 和 SSIM 绝对门槛。

### 7.1 质量驱动 Capped CRF / VBV

质量驱动码控不使用人为设定的低目标码率制造节省。执行顺序为：

1. 先用自适应 CRF 搜索找到满足模式画质门槛的最高 CRF，得到无上限自然码率。
2. 只统计视频流压缩包字节，计算自然平均码率和 1 秒时间窗峰值。
3. 以“自然平均码率×模式峰值倍率”生成 `vbv-maxrate`，以“maxrate×缓冲秒数”生成 `vbv-bufsize`。
4. 在同一 CRF 下重新编码，检查 VMAF、P5、SSIM、相对无上限 CRF 的 VMAF 变化和码率变化。
5. 画质失败时每次将峰值倍率放宽 0.25，最多放宽到 2.5；选择第一个同时保持画质并改善平均/峰值码率的设置。
6. 所有候选失败时回退无上限 CRF，不输出虚假的码控成功结论。

三模式初始策略：

| 模式 | 初始 `maxrate/自然平均码率` | VBV 缓冲时长 |
|---|---:|---:|
| 保守 `conservative` | 2.00 | 4秒 |
| 综合 `balanced` | 1.50 | 3秒 |
| 激进 `aggressive` | 1.25 | 2秒 |

CRF 模式必须同时设置 `vbv-maxrate` 和 `vbv-bufsize` 才启用 VBV。暂不设置 `crf-max`，因为它与过紧 VBV 组合可能造成缓冲下溢；质量保护由编码后的客观指标复核承担。长期跨片段平均码率反馈控制尚未实现，不得把单片段平均码率称为长期闭环。

### 7.2 AQ 自适应量化

当前 `libx265 + medium` 已默认使用 AQ2、strength 1.0、qg-size 32 和 CU-tree，AQ 研究必须把它作为明确对照，不能把编码器已有默认能力算成新增收益。[x265 官方 AQ 参数说明](https://x265.readthedocs.io/en/latest/cli.html?highlight=hevc-aq#cmdoption-aq-mode)

本阶段只研究 x265 内置的两个独立候选：

- AQ3：自动方差并偏向暗场，用于研究低照度色带和块效应风险。
- AQ4：自动方差并加入边缘信息，用于研究结构边缘的码率重分配。

三模式参数：

| 模式 | AQ strength | qg-size | 候选 |
|---|---:|---:|---|
| 保守 `conservative` | 0.8 | 32 | AQ3、AQ4 |
| 综合 `balanced` | 1.0 | 16 | AQ3、AQ4 |
| 激进 `aggressive` | 1.2 | 16 | AQ3、AQ4 |

每个 AQ 候选必须独立执行 CRF 画质边界搜索，再与默认 AQ2 的边界候选执行等画质配对。两路先通过模式的 VMAF、P5、SSIM；保守/综合还必须通过 0.97x 速度门槛，激进模式不设速度门槛；随后要求 `|ΔVMAF|≤1.0`。只有取得严格正向平均码率收益时才采用，否则回退 AQ2。

内置 AQ 只根据方差、暗场或边缘信息调整块级 QP，不识别人、机器人或文字语义。`aq-motion` 仍是实验功能，且官方定义为相对运动越大时使用更多量化，并不等同于保护运动区域，因此当前关闭。空间 ROI 质量图和语义 ROI 编码不在本阶段冒充完成。

### 7.3 固定机位静态 ROI

静态 ROI 作为 V1.0 任务 5C 的独立研究项，仅适用于机位和分辨率不变的监控画面。程序使用 FFmpeg `addroi` 向解码帧附加 ROI side data，由 libx265 转换为空间量化偏移；Main 8-bit 下 `qoffset = QP delta / 51`。保持 AQ2、strength 1.0、qg-size 32 和当前帧间参数不变，对应 16×16 量化块粒度。

ROI 配置必须包含版本、摄像头 ID、参考分辨率和区域列表。区域 ID 必须唯一，坐标和尺寸必须为正确整数且不越界；输入分辨率不匹配时直接报错，不静默缩放。角色只允许 `critical`、`evidence`、`normal` 和 `discard`。

三种模式 QP 偏移：

| 模式 | critical | evidence | normal | discard |
|---|---:|---:|---:|---:|
| 保守 `conservative` | -2 | -2 | +1 | +3 |
| 综合 `balanced` | -3 | -2 | +3 | +5 |
| 激进 `aggressive` | -4 | -3 | +5 | +8 |

FFmpeg/libx265 在区域重叠时使用 ROI 列表中的第一个区域，因此滤镜链固定按 `evidence → critical → discard → 全画面 normal` 输出。每个候选缓存键加入 ROI 配置哈希、区域坐标/角色和模式 QP 策略；无 ROI 候选仍保持旧缓存兼容。

`roi-study` 不在相同 CRF 下强行比较。无 ROI AQ2 对照和 ROI 候选必须分别执行 CRF 18～38、步长 0.5 的画质边界搜索，并在两路最高合格 CRF 及低一档相邻点中配对。仅在以下条件全部通过时选中 ROI：

- 全局 VMAF、VMAF P5、SSIM 通过所选模式门槛；保守/综合还须通过 0.97x 速度门槛，激进模式不设速度门槛。
- 两路全局 `|delta VMAF| <= 1.0`。
- 每个 `critical` 区域相对对照的 VMAF 下降不超过 0.5，VMAF P5 下降不超过 1.0。
- 每个 `evidence` 区域的 SSIM 下降不超过 0.002。
- ROI 平均视频包码率严格低于无 ROI 对照。

任一条件失败时回退无 ROI AQ2 对照，不生成部署文件。报告同时保存全局指标和通过 crop 后复算的重点区域指标，以防全局平均值掩盖局部质量损失。

### 7.4 ROI 保护的无效噪声抑制

噪声会制造无法预测的高频变化，使编码器将比特浪费在夜间增益噪点、传感器热噪声和静态背景闪烁上。V1.0 任务 5D 先研究 FFmpeg `hqdn3d` 的轻量空域+时域降噪；它的目标是提高可压缩性，不是对全图模糊。

本阶段复用固定机位 ROI 配置做区域级处理：全画面 `normal` 作为中等降噪底图，`discard` 静态背景用较强降噪，`critical` 只做轻度降噪，`evidence` 时间戳使用原图直通。区域重叠时按 `normal → discard → critical → evidence` 的覆盖顺序合成，使证据区最后覆盖。

`hqdn3d` 参数顺序为 `luma_spatial:chroma_spatial:luma_tmp:chroma_tmp`：

| 模式 | critical | normal | discard | evidence |
|---|---|---|---|---|
| 保守 | `0.2:0.15:0.3:0.225` | `0.8:0.6:1.2:0.9` | `1.2:0.9:1.8:1.35` | 原图直通 |
| 综合 | `0.4:0.3:0.6:0.45` | `1.2:0.9:2.0:1.5` | `1.8:1.35:3.0:2.25` | 原图直通 |
| 激进 | `0.6:0.45:1.0:0.75` | `1.8:1.35:3.0:2.25` | `2.6:1.95:4.0:3.0` | 原图直通 |

降噪研究保持当前帧间参数、AQ2/strength 1.0/qg-size 32 和 Main 8-bit 不变，且不叠加任务 5C 中已被回退的 ROI QP 偏移，避免把两种变量的收益混在一起。无降噪对照和降噪候选分别执行 CRF 18～38、步长 0.5 的画质边界搜索，并在边界候选中配对。仅在以下条件全部通过时选中降噪：

- 全局 VMAF、VMAF P5、SSIM 通过所选模式门槛；保守/综合还须通过 0.97x 速度门槛，激进模式不设速度门槛；两路 `|delta VMAF| <= 1.0`。
- 每个 `critical` 区域 VMAF 下降不超过 0.5、P5 下降不超过 1.0。
- 每个 `evidence` 区域 SSIM 下降不超过 0.002。
- 降噪候选的平均视频包码率严格低于无降噪对照。

任一条件失败时回退无降噪 AQ2 对照。当前只处理随机空时噪声，不宣称已解决雨雪、烟雾、光线闪烁或过度锐化边缘振铃；增益、曝光和锐化优先应在摄像头侧控制，本离线工具不伪造已完成这些能力。

### 7.5 激进模式 x265 slow 研究

任务 5E 将保守/综合模式保持为 `medium`，只把激进模式改为 `slow`。x265 preset 改变的是编码搜索深度和压缩效率；不能在相同 CRF 下直接把码率差当成等画质收益。

`preset-study` 固定激进模式的帧间参数、AQ2、Main 8-bit、像素格式和统一参考片段，让 `medium` 对照与 `slow` 候选分别执行 CRF 18～38、步长 0.5 的独立画质边界搜索。两路先通过激进模式 VMAF、P5、SSIM 绝对门槛，再在最高合格点及低一档相邻点中寻找 `|ΔVMAF|≤1.0` 的配对。激进模式不因编码速度较低而拒绝候选，但报告必须给出编码耗时、速度等级以及 slow 相对 medium 的平均视频包码率变化。

该受控研究只证明 preset 在固定参数下的压缩效率差异。正式 `compare --mode aggressive` 仍须让工程基线和帧间优化组合共同使用 `slow`，从而把帧间参数收益与 preset 收益分离。slow 没有取得等画质码率收益时不得宣称其有效，但模式仍按用户决策保持 `slow`，并标记“本素材未证实 preset 收益”。

## 8. 码率口径

- 核心码率只统计视频流包字节，不使用包含音频和封装开销的总文件码率。
- 平均码率统一按 `视频包字节总和×8÷视频时长` 计算，不优先采用容器或流头部声明的 `bit_rate`。
- 质量驱动码控另外统计固定 1 秒时间窗的峰值和 P95 码率。
- 同时记录文件大小、容器总码率和视频流峰值，但不混入口径。
- 输入源流、工程基线和优化组合使用相同的统计方法。

算法节省率：

```text
(工程基线H.265码率 - 优化组合H.265码率)
÷ 工程基线H.265码率 × 100%
```

源流节省率：

```text
(输入参考视频码率 - 优化组合H.265码率)
÷ 输入参考视频码率 × 100%
```

## 9. 三种独立结论

### 9.1 算法可行性

通过条件：

- 找到等画质候选对。
- 优化组合相对工程基线达到所选模式的算法节省门槛；保守模式也必须取得严格正收益。

该结论只能表述为：优化组合在本样本、当前 libx265 和实验参数下有效。

### 9.2 软件画面连续性

软件画面连续性首先要求：

- 优化输出能够全文件解码，无损坏或解码错误。
- 输出分辨率、帧率、帧数和时长与统一参考片段一致。
- 逐帧显示时间戳从零开始、严格递增，最大间隔不超过 `1.5 × 标准帧间隔`。
- 保守/综合模式的优化组合编码速度不低于 `0.97x`，允许在有限片段或短时近实时场景中用缓存吸收约 3% 的处理差距。
- 激进模式不设速度硬门槛，只要文件连续性通过即可通过本项；低于 1.0x 时必须标记为离线编码方案。

保守/综合模式速度达到 `0.97x` 但低于 `1.0x` 时，只能表述为“有限片段可近实时连续处理，需要累积少量延迟”；低于 `0.97x` 时不通过持续处理能力门槛。所有模式不低于 `1.0x` 才能表述为“当前电脑可实时处理”，不低于 `1.1x` 时额外标记为“具有工程余量”。激进模式低于 `1.0x` 仍可通过文件连续性，但只能表述为离线编码。该结论不代表摄像头硬件负载。

### 9.3 部署可行性

V1.0 只给出初步筛选：

- 优化组合相对输入源流达到所选模式的源流节省门槛：标记“值得进入摄像头实机验证”。
- 未达到所选模式门槛：标记“原码流直通”。

无论哪种结果，摄像头侧正式结论都必须等待厂商硬件实机验证。

## 10. 命令接口

当前用于验证单方案搜索的开发接口：

```powershell
python -m hevc_lab search-crf `
  --input '监控视频.mp4' `
  --output '.\results\single-search' `
  --mode balanced `
  --scheme optimized `
  --start 0 `
  --duration 15
```

该接口的 `--scheme` 一次只接受 `baseline` 或 `optimized`。它生成 `search.json`、`quality_points.csv` 和 `search_summary.md`，用于验证单方案边界；不替代下面规划的最终 `compare` 接口。

当前用于验证双方案独立搜索和配对的开发接口：

```powershell
python -m hevc_lab pair-crf `
  --input '监控视频.mp4' `
  --output '.\results\pair-search' `
  --mode balanced `
  --start 0 `
  --duration 15
```

配对候选只取每个方案的最高合格 CRF 及其低一档相邻合格点，避免用远高于画质门槛的低 CRF 锚点制造无意义的 VMAF 接近。两路必须先通过模式绝对画质门槛，再要求 `|ΔVMAF|≤1.0`；否则报告为“证据不足”。

当前用于验证质量驱动码控的开发接口：

```powershell
python -m hevc_lab rate-control `
  --input '监控视频.mp4' `
  --output '.\results\rate-control' `
  --mode balanced `
  --scheme optimized `
  --duration 15
```

该接口生成 `rate_control.json`、`rate_control_points.csv` 和 `rate_control_summary.md`，只作为离线码控研究，不等同于实时平台长期平均码率控制。

当前用于验证 AQ 自适应量化的开发接口：

```powershell
python -m hevc_lab aq-study `
  --input '监控视频.mp4' `
  --output '.\results\aq-study' `
  --mode balanced `
  --scheme optimized `
  --duration 15
```

该接口让默认 AQ2、暗场 AQ3 和边缘 AQ4 分别搜索自己的画质边界，生成 `aq_study.json`、`aq_quality_points.csv` 和 `aq_study_summary.md`。没有等画质码率收益时回退默认 AQ2。

当前用于验证固定机位静态 ROI 的开发接口：

```powershell
python -m hevc_lab roi-study `
  --input '监控视频.mp4' `
  --roi-config '.\configs\camera-entrance-roi.json' `
  --output '.\results\roi-study' `
  --mode balanced `
  --scheme optimized `
  --duration 12
```

该接口生成 `roi_study.json`、`roi_quality_points.csv`、`roi_region_quality.csv`、`roi_study_summary.md` 和 `roi_overlay.png`。它独立搜索无 ROI/ROI 画质边界，通过全局和重点区域指标共同决策；无收益时明确回退无 ROI 对照。

当前用于验证 ROI 保护降噪的开发接口：

```powershell
python -m hevc_lab denoise-study `
  --input '监控视频.mp4' `
  --roi-config '.\configs\camera-entrance-roi.json' `
  --output '.\results\denoise-study' `
  --mode balanced `
  --scheme optimized `
  --duration 12
```

该接口生成 `denoise_study.json`、`denoise_quality_points.csv`、`denoise_region_quality.csv`、`denoise_study_summary.md` 和 `denoise_overlay.png`。它不叠加已回退的 ROI QP 偏移；无降噪/降噪独立搜索后，只有全局、critical/evidence 局部画质、速度和平均视频包码率全部通过才选中降噪。

当前用于验证激进模式 x265 slow 压缩效率的开发接口：

```powershell
python -m hevc_lab preset-study `
  --input '监控视频.mp4' `
  --output '.\results\preset-study' `
  --mode aggressive `
  --scheme optimized `
  --duration 12
```

该接口固定帧间参数及其他编码条件，让 `medium` 对照和 `slow` 候选分别搜索 CRF 画质边界并等画质配对，生成 `preset_study.json`、`preset_quality_points.csv`、`preset_pair.csv` 和 `preset_study_summary.md`。激进模式不使用速度淘汰候选；报告必须保留速度等级并把低于1.0x的结果标记为离线编码。

当前正式比较接口：

```powershell
python -m hevc_lab compare `
  --input '监控视频.mp4' `
  --output '.\results\camera01-v1' `
  --mode balanced `
  --start 0 `
  --duration 15 `
  --target-vmaf 95 `
  --target-vmaf-p5 93 `
  --target-ssim 0.99 `
  --min-saving 5
```

`compare` 串联统一参考、两路独立搜索、等画质配对、输出连续性检查和三种正式结论。`--min-saving` 同时覆盖算法和源流门槛，`--min-algorithm-saving`、`--min-source-saving` 可分别覆盖且优先级更高。

输出目录中的 `comparison_state.json` 使用输入内容哈希、VMAF模型哈希、版本、模式和全部有效参数生成实验缓存键，并原子记录当前阶段、状态、尝试次数和失败原因。相同请求中断或失败后重跑，会复用FFV1参考及已完成的CRF候选/指标；状态为完成且候选文件仍存在时，直接读取 `comparison.json`，不再搜索或解码。

规划新增网页接口：

```powershell
python -m hevc_lab web --host 127.0.0.1 --port 8000
```

现有 `experiment` 命令可以在迁移完成前保留为内部原型，但不得作为 V1.0 最终入口。

## 11. 网页设计

### 输入区

- 标准文件选择按钮。
- 保守、综合、激进三种模式选择；默认综合模式。
- 片段开始时间与时长。
- 画质门槛；默认收起在高级设置。
- 不提供“目标码率比例”。

### 进度区

```text
准备统一参考画面
搜索工程基线CRF
搜索优化组合CRF
匹配等画质候选
生成H.265结果和浏览器预览
生成报告
```

### 核心对比区

| 指标 | 输入参考 | 工程基线 H.265 | 优化组合 H.265 |
|---|---:|---:|---:|
| 编码格式 | 显示 | H.265 | H.265 |
| 最终 CRF | — | 自动选择 | 自动选择 |
| 视频码率 | 显示 | 显示 | 显示 |
| VMAF | 参考 | 显示 | 显示 |
| VMAF P5 | 参考 | 显示 | 显示 |
| SSIM | 参考 | 显示 | 显示 |
| GOP | 显示/未知 | 2秒 | 显示所选模式的 2/4/6秒 |
| 编码速度 | — | 显示 | 显示 |

页面突出显示：算法节省率、源流节省率、画质差、三种结论及失败原因。

### 视频观看

- 两个 H.265 结果保留为课题正式产物。
- 额外生成高质量 H.264 浏览器预览，预览不参与指标和码率结论。
- 两路预览支持同步播放、暂停、拖动和同位置截图。

## 12. 输出文件

```text
results/<experiment-id>/
├─ comparison_state.json
├─ comparison.json
├─ reference/
│  ├─ reference_lossless.mkv
│  └─ reference.json
├─ baseline/
│  ├─ candidates/
│  └─ selected_baseline.mp4
├─ optimized/
│  ├─ candidates/
│  └─ selected_optimized.mp4
├─ previews/
│  ├─ baseline_preview.mp4
│  └─ optimized_preview.mp4
├─ work/
│  ├─ metrics/
│  └─ logs/
├─ quality_points.csv
├─ comparison.csv
├─ result.json
└─ summary.md
```

## 13. 第一版验收标准

- 同一个参考帧序列能够生成两种 H.265 结果。
- 命令行和网页都能选择保守、综合、激进模式，默认综合模式，报告能够复现模式及覆盖参数。
- 两种方案分别搜索自己的合格 CRF，不再用相同 CRF 代替等画质。
- 输出分辨率、帧率、帧数和时长与参考一致。
- VMAF、VMAF P5、SSIM和视频流码率可以复算。
- 两路分别通过绝对画质门槛且 VMAF 差值绝对值不超过 1.0，否则标记证据不足。
- 静态 ROI 候选必须独立搜索 CRF，全局画质、critical/evidence 局部画质和平均视频码率全部通过才可选中。
- ROI 无码率收益或局部画质失败时正确回退无 ROI 对照，不生成部署文件。
- ROI 保护降噪候选必须独立搜索 CRF，并在证据区原图直通、关键区轻度降噪的前提下通过全局和局部指标；无严格正码率收益时回退无降噪对照。
- 算法收益、源流收益、画面连续性和编码速度分别呈现；编码速度低于 `1.0x` 不等同于输出画面不连续。
- 负收益时不会生成“部署成功”结论。
- 网页可以上传视频、查看进度、同步观看两路结果并下载报告。
- 中文和空格路径可用，中断后可继续。
- JSON、CSV、Markdown和网页数据一致。
- 单元测试覆盖搜索、缓存、质量匹配、码率口径、结论判断和异常处理。
- 至少使用一个合成样本和一个真实固定监控片段完成端到端验证。

## 14. 后续版本

- V1.1：参考帧、B 帧、lookahead、GOP逐项消融。
- V1.2：完整率失真曲线和 BD-Rate。
- V2.0：摄像头控制适配层和厂商参数映射。
- V3.0：摄像头—网关—平台实时闭环。
