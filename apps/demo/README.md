# V1.6 静态展示版

这个目录是给 Cloudflare Pages 使用的纯静态展示版。它不上传视频、不编码视频、不调用 FastAPI，也不依赖 Python、FFmpeg、任务队列或本地工作目录。

## 目录结构

```text
apps/demo/
├─ index.html
├─ styles.css
├─ app.js
├─ data/
│  ├─ results.json
│  └─ final_metrics.csv
└─ videos/
   ├─ default_preview.mp4
   ├─ conservative_preview.mp4
   └─ conservative_hevc.mp4
```

`data/results.json` 是页面主要数据源。当前展示 V1.6 两路离线结果：H.264 原生编码和 H.265 固定参数方案。蓝色参数标签固定在分割竖线右侧；接近右边界时由舞台容器自然裁切，不拉伸、不换行。

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
