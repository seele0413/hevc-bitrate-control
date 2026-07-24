# H.265 静态展示版

这个目录是给 Cloudflare Pages 使用的纯静态展示版。它不上传视频、不编码视频、不调用 FastAPI，也不依赖 Python、FFmpeg、任务队列或本地工作目录。

## 目录结构

```text
apps/demo/
├─ index.html
├─ data/
│  ├─ results.json
│  └─ final_metrics.csv
└─ videos/
   ├─ previews/   # H.264 浏览器预览
   └─ hevc/       # H.265/HEVC 正式下载文件
```

`data/results.json` 是页面主要数据源。当前展示“监控素材二”的两路离线结果：x265 原生默认和通用无 ROI / 保守方案。H.264 预览应从对应的 H.265/HEVC 正式结果转码生成，不要把原始输入视频混作某一路编码预览。

## Cloudflare Pages 设置

```text
Framework preset: None
Build command: echo "no build"
Build output directory: apps/demo
```

也可以把 Build command 留空。预览 MP4 建议单文件小于 25MiB；当前 `videos/` 下的演示文件均低于该限制。

## 本地预览

从项目根目录运行：

```powershell
& 'C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe' -m http.server 8081 -d apps/demo
```

然后打开：

```text
http://127.0.0.1:8081/
```
