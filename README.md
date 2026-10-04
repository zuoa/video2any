# Video to Any

一个面向视频转换的多工具 Web 项目。主页提供统一工具入口，每个工具保持独立工作台，同时复用整体导航、缓存和输出目录。

## 功能

- 主页 `/#/` 提供工具选择入口，目前包含视频转 GIF、BV 音频片段提取、视频总结和讲课视频转练习题。
- GIF 工具 `/#/gif` 支持上传本地视频，或输入 Bilibili BV 号 / `bilibili.com/video/BV...` 地址下载视频；URL 会自动提取 BV 与 `p` 参数，多分 P 视频可选择具体分 P。
- 在视频上拖拽框选裁剪区域，预览阶段使用遮罩显示选区。
- 可通过播放位置滑块定位片段，使用开始时间和持续秒数控制导出范围。
- 支持输出宽度、帧率、变速、循环播放设置，导出后自动下载 GIF。
- 支持单个基础文字层：内容、位置、字号、颜色、描边、背景框。
- 支持循环或非循环 GIF。
- 独立音频工具页 `/#/audio` 支持输入 BV 号 / Bilibili URL、选择分 P、先下载视频、设置开始和结束时间、试听片段，可选择音频增强，并导出 `mp3`、`m4a` 或 `wav` 音频片段。
- 视频总结工具页 `/#/summary` 输入 BV 号 / Bilibili URL、选择分 P，自动拉取该视频完整的 CC 字幕，用大模型生成结构化总结：整体总结、带时间点的关键内容点（可点击跳转到对应时间）、金句/知识点提炼，支持一键复制 Markdown、下载完整字幕文件。长视频会分段总结后再合并，避免截断丢失内容。仅支持带 CC 字幕（人工或 AI 字幕）的视频。
- 上传和下载的原视频会保留用于连续制作多个 GIF；同一个 BV 的同一分 P 会复用本地源文件，服务会自动清理 24 小时未使用的原视频文件。
- Docker 单容器部署，运行时文件通过 `/data` 持久化；Compose 默认映射到宿主机目录。

## Docker 运行

```bash
docker build -t video2any .
docker run --rm -p 8000:8000 -v video2emoticon-data:/data video2any
```

打开 `http://localhost:8000`。

也可以使用 Compose：

```bash
mkdir -p data/uploads data/downloads data/outputs data/fonts data/exercises data/models cookies
docker compose pull
docker compose up
```

Compose 默认使用目录映射：

```text
./data/uploads   -> /data/uploads
./data/downloads -> /data/downloads
./data/outputs   -> /data/outputs
./data/fonts     -> /data/fonts
./cookies        -> /data/cookies
```

字体文件可以通过页面上传，也可以直接放入 `data/fonts/` 后在页面点击刷新。支持 `.ttf`、`.otf`、`.ttc`、`.otc`。

## Bilibili Cookie

Bilibili 可能返回 `HTTP Error 412: Precondition Failed`，这通常需要带登录态 cookie 下载。

推荐方式是使用 Netscape 格式的 cookies 文件：

```bash
mkdir -p cookies
# 将导出的 Bilibili cookies 保存为 cookies/bilibili.cookies.txt
docker compose up
```

也可以直接传浏览器请求里的原始 `Cookie` header：

```bash
docker run --rm \
  -p 8000:8000 \
  -v video2emoticon-data:/data \
  -e BILIBILI_COOKIE_HEADER='SESSDATA=...; bili_jct=...; DedeUserID=...' \
  video2any
```

还支持把 Netscape cookies 文件内容放到 `BILIBILI_COOKIES` 环境变量中，服务会写入 `/data/cookies/bilibili.cookies.txt` 后交给 `yt-dlp`。

`yt-dlp` 会在读取 cookies 文件后写回更新后的 cookie jar，因此 Compose 里的 `cookies/` 挂载需要保持可写。不要把 cookie 提交到 GitHub；`cookies/` 已加入 `.gitignore`。

## 本地开发

后端：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

前端开发服务默认代理 `/api` 到 `http://127.0.0.1:8000`。

## GitHub Actions 镜像

`.github/workflows/docker.yml` 会在以下场景构建镜像：

- push 到 `main` 或 `master`
- push `v*` tag
- pull request 构建校验
- 手动触发 `workflow_dispatch`

非 pull request 事件会推送到：

```text
ghcr.io/<owner>/video2any:latest
ghcr.io/<owner>/video2any:<branch-or-tag>
ghcr.io/<owner>/video2any:sha-<commit>
```

部署时挂载 `/data`：

```bash
docker run -p 8000:8000 -v video2emoticon-data:/data ghcr.io/<owner>/video2any:latest
```

如果用 Compose 部署，按本仓库的 `docker-compose.yml` 会映射到当前目录下的 `data/` 和 `cookies/`。

## 环境变量

- `DATA_DIR`：运行时数据目录，默认 `/data`。
- `FRONTEND_DIST`：前端静态文件目录，镜像内默认 `/app/frontend/dist`。
- `FONT_FILE`：FFmpeg `drawtext` 使用的字体文件路径。
- `/data/fonts`：页面可扫描的字体目录，用于文字层字体选择和预览。
- `CORS_ORIGINS`：开发时允许的跨域来源，默认 `*`。
- `BILIBILI_COOKIES_FILE`：Netscape 格式 Bilibili cookies 文件路径，推荐挂载到 `/data/cookies/bilibili.cookies.txt`。
- `BILIBILI_COOKIE_HEADER`：浏览器请求里的原始 `Cookie` header，例如 `SESSDATA=...; bili_jct=...`。
- `BILIBILI_COOKIES`：Netscape cookies 文件内容，适合通过部署平台 Secret 注入。
- `OPENAI_API_KEY`：视频总结工具调用大模型用的 API Key（必填，否则总结页会返回 503）。
- `OPENAI_BASE_URL`：大模型 OpenAI 兼容接口地址，默认 `https://api.deepseek.com`。
- `OPENAI_MODEL`：模型名，默认 `deepseek-chat`。
- `OPENAI_TIMEOUT`：单次大模型请求超时（秒），默认 `60`。
- `SUMMARY_MAX_INPUT_CHARS`：总结分段时每段的字幕字符预算，默认 `9000`（越长越完整，但更慢更费 token）。

## 运行时依赖

镜像内置：

- Python 3.12
- FFmpeg
- yt-dlp
- DejaVu 与 Noto 中文字体
- Pandoc（Word 原生公式导出）
- faster-whisper（CPU 语音识别）

Bilibili 下载取决于部署环境网络可达性和 cookie 有效性。部分视频如果需要会员、地区权限或 cookie 过期，yt-dlp 可能无法下载，后端会把失败原因返回给前端。

## 讲课视频转练习题

`/#/exercises` 支持上传讲课视频，或输入 B 站 BV / URL 并选择单个分 P。下载或上传完成后，在服务端 CPU 上通过 **faster-whisper** 识别语音，再使用现有 `OPENAI_*` 配置提炼知识点。

1. 查看带时间戳的转写，校对术语、补充没有念出的板书公式；保存后重新提炼知识点。
2. 编辑知识点并保存，勾选出题范围。
3. 选择单选、填空、简答、计算题，设置基础 / 巩固 / 提高难度及生成数量（1–30，默认 10）。
4. 预览候选题及答案解析，勾选需要的题目，分别下载 **练习卷 Word** 和 **答案解析 Word**。

两份文件按相同顺序连续编号，使用 A4 黑白排版；练习卷保留答题空间。网页支持 Markdown 与 LaTeX，Word 通过 Pandoc 转换为可编辑的原生公式，支持分式、根号、上下标、积分、求和、矩阵与分段函数。使用 `$...$` / `$$...$$`，不支持自定义宏或 LaTeX 绘图。候选题只预览和勾选，不提供题目编辑器；可以重新生成独立批次，旧题目及其知识点版本会保留。

长视频按完整转写分段提炼，不截掉后半段。只分析语音文字，不自动识别 PPT 或板书画面；可手动补充。题数少于所选知识点时，从选定范围均匀取点。模型生成后会复核答案和解析，仍建议在打印前查看候选题。

转写和出题为后台任务，页面显示阶段及进度，刷新后恢复最近课程；页面地址中的 `lesson` 参数也可以重新打开该课程。错误可重试，已完成的转写会复用。课程文字、知识点与题目批次保存在 `/data/exercises/exercises.db`，与 24 小时原视频缓存独立；处理中的原视频不会被清理。服务重启会把未完成任务标记为中断，可手动重试。

新增环境变量：

- `WHISPER_MODEL`：默认 `small`（多语言）；也可传本地 CTranslate2 模型目录。
- `WHISPER_CPU_THREADS`：CPU 推理线程数，默认 `4`，转写任务串行执行。
- `WHISPER_MODEL_DIR`：模型缓存路径，默认 `DATA_DIR/models`。
- `EXERCISE_MAX_INPUT_CHARS`：知识点提炼每段原文预算，默认 `9000`。

Docker 镜像包含 Pandoc 和 Noto 中文字体。Compose 持久挂载 `./data/exercises` 与 `./data/models`。首次使用需能访问 Hugging Face 下载模型；离线部署可预先放入转换好的模型并配置 `WHISPER_MODEL`。语音识别不需要 GPU 或外部转写接口；知识点与出题需要有效的 `OPENAI_API_KEY`。

本地运行 Word 导出还需安装 Pandoc 和 Noto Serif CJK SC 字体（Linux 可安装 `pandoc fonts-noto-cjk`，macOS 可使用 Homebrew 安装 Pandoc 并安装 Noto 中文字体）。请保持单个 Uvicorn worker；本模块的工作池及清理保护由单进程管理。

验证：

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest backend/tests
cd frontend
npm run build
```

Word 集成测试需要 Pandoc；也可在测试环境安装 `pypandoc_binary`，不影响生产依赖。
