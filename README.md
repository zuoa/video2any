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
- 上传和下载的原视频会保留用于连续制作多个 GIF；同一个 BV 的同一分 P 会复用本地源文件，服务会自动清理 24 小时未使用且未关联课程的原视频文件；课程原视频随课程保留。
- Docker 单容器部署，运行时文件通过 `/data` 持久化；Compose 默认映射到宿主机目录。

## Docker 运行

```bash
docker build -t video2any .
docker run --rm -p 8000:8000 -v video2emoticon-data:/data video2any
```

打开 `http://localhost:8000`。

也可以使用 Compose：

```bash
mkdir -p data/uploads data/downloads data/outputs data/fonts data/exercises data/models data/admin cookies
docker compose pull
docker compose up
```

Compose 默认使用目录映射：

```text
./data/uploads   -> /data/uploads
./data/downloads -> /data/downloads
./data/outputs   -> /data/outputs
./data/summaries -> /data/summaries
./data/exercises -> /data/exercises
./data/models    -> /data/models
./data/fonts     -> /data/fonts
./data/admin     -> /data/admin
./cookies        -> /data/cookies
```

字体文件可以通过页面上传，也可以直接放入 `data/fonts/` 后在页面点击刷新。支持 `.ttf`、`.otf`、`.ttc`、`.otc`。

## 隐藏管理后台

管理入口为 `/#/_manage`，首页和工具导航不显示入口，管理接口不出现在公开的 OpenAPI 文档中。后台可管理 Bilibili Cookie、刷新令牌和保活参数，以及大模型 API Key、接口地址、模型、超时与站点分享地址。

首次使用时，在服务器创建管理员；没有默认密码，也没有公开注册接口：

```bash
# Compose 部署（先启动包含后台功能的新镜像）
docker compose exec app python -m backend.app.admin_auth setup --username admin
# 本地开发，在项目根目录执行；DATA_DIR 须与后端进程一致
.venv/bin/python -m backend.app.admin_auth setup --username admin
```

命令会交互输入并确认密码，至少 12 个字符。Compose 部署后访问 `http://localhost:8005/#/_manage`，使用创建的账号登录。忘记密码时，将命令中的 `setup` 改为 `reset`；重置和后台修改密码都会使全部管理会话失效。

配置保存后生效，无需改 `.env` 或重建容器；已保存的配置优先于环境变量，重启后仍然保留。大模型新调用使用新的连接设置；正在进行的调用继续使用原连接。基础设施和本地识别模型的配置（数据目录、端口、ASR 后端等）仍由部署配置控制。

管理员密码采用带随机盐的 PBKDF2-SHA256 哈希，登录会话由服务器保存，8 小时过期，支持退出撤销。Cookie 使用 HttpOnly、SameSite=Strict，HTTPS 下启用 Secure；修改配置需要 CSRF 校验和同源校验，登录失败有频率限制。已保存的 Cookie、令牌、API Key 不会通过管理接口回显。隐藏入口用于减少曝光，实际访问权限由后端鉴权控制。

持久保留 `data/admin/`（管理员、会话及设置）和 `cookies/`（Bilibili 凭据），Compose 已挂载。密钥保存在服务端文件中，数据库和凭据文件权限为 `0600`，管理员目录为 `0700`。生产环境通过 HTTPS 访问；反向代理应保留原始 Host 并正确传递协议，使同源校验和 Secure Cookie 正常工作。

## Bilibili Cookie

Bilibili 下载和字幕接口共用一份登录态。服务启动时检查 Cookie，之后默认每 24 小时检查一次；Bilibili 返回需要刷新时，自动执行 Web Cookie 续期。临时网络错误和风控响应保留凭据，默认 5 分钟后重试；账号确实失效时提示重新登录。

推荐在隐藏管理后台的「Bilibili 账号」页导入凭据：

1. 登录 Bilibili，复制浏览器 Cookie，或导出 Netscape / JSON 格式文件。至少需要 `SESSDATA`，自动续期还需 `bili_jct`，建议保留 `DedeUserID`、`buvid3`、`buvid4`。
2. 在同一个浏览器打开开发者工具 → Application / 应用 → Local Storage / 本地存储 → `https://www.bilibili.com`，复制 `ac_time_value`，填入「刷新令牌」。Cookie 和令牌必须来自同一次登录，仅导出 Cookie 无法续期。
3. 保存登录凭据，点击「检查并续期」查看状态；按需调整自动续期的检查间隔、失败重试和接口请求间隔。

重新登录后，在后台重新导入 Cookie 和对应的刷新令牌。更换登录态时，未同时提交新令牌会清除原令牌，避免混用；仅补充令牌可保留原 Cookie。移除账号后，旧环境变量不会重新恢复账号。

已有部署也兼容直接挂载 Netscape Cookie 文件：

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

还支持把 Netscape cookies 文件内容放到 `BILIBILI_COOKIES` 环境变量中。环境变量仅在 Cookie 文件不存在时初始化文件；已有文件优先，避免每次请求或重启用旧 Cookie 覆盖续期结果。原始 `Cookie` header 也只在文件中没有登录态时使用。在后台导入或移除账号后，以后台保存的登录态为准。

如果尚未通过后台管理账号，仍可用环境变量初始化刷新令牌和默认参数：

```dotenv
BILIBILI_REFRESH_TOKEN=同一次登录的ac_time_value
BILIBILI_KEEPALIVE_ENABLED=true
BILIBILI_CHECK_INTERVAL_SECONDS=86400
BILIBILI_RETRY_INTERVAL_SECONDS=300
```

```bash
docker compose up -d --force-recreate app
```

后台显示缓存状态，不触发上游请求；「检查并续期」才主动检查。`healthy` 表示检查成功且具备刷新令牌，`missing_refresh_token` 表示当前有效但无法续期，`refresh_required` 表示需要补充令牌，`expired` 表示需要重新导入凭据，`retrying` 表示临时失败。刷新返回 `86095` 时先重新获取刷新口令再重试，持续失败按短间隔重试。旧 `/api/bilibili/session` 接口也需要管理员登录。本地 `.env` 不会自动读取，但可以完全通过后台保存这些运行设置。

新 Cookie 和匹配的新令牌会一起原子保存到 Cookie 文件旁的 `bilibili.cookies.session.json`，再写回 `bilibili.cookies.txt` 并确认刷新；确认请求失败后会使用已保存的新登录态继续恢复。后台检查与业务请求通过线程锁和文件锁协调，多个进程不会同时轮换令牌。下载任务使用独立临时 Cookie 文件，完成后删除，避免 `yt-dlp` 将旧凭据写回主文件。Compose 中的 `cookies/` 挂载需要可写并保留到下次部署。

服务会优先使用保存的续期结果，因此无需每次刷新后修改 `.env`。Bilibili 密码变更、手动退出或风控强制下线仍需要重新登录，自动续期无法恢复已被撤销的会话。

可在容器内手动检查一次（输出仅包含状态）：

```bash
docker compose exec app python -m backend.app.bili_auth
```

HTTP 412 也可能由网络出口或请求频率触发，不会直接被当作账号失效。续期协议参考 [bilibili-API-collect](https://github.com/pskdje/bilibili-API-collect/blob/main/docs/login/cookie_refresh.md) 和 [defy997/bilibili-api](https://github.com/defy997/bilibili-api/blob/main/refresh_local.py)；凭据保存与网络故障处理同时参考 [bilibili-cli](https://github.com/public-clis/bilibili-cli/blob/main/bili_cli/auth.py)。不要把 Cookie 或会话文件提交到 GitHub；`cookies/` 和 `data/` 已加入 `.gitignore`。

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

前端使用 React + TypeScript + Vite，UI 组件库为 Radix UI Themes。全局主题在 `frontend/src/main.tsx` 配置，品牌色和组件样式在 `frontend/src/ui.css`；表单、按钮、弹窗和进度条统一使用 Radix 组件。`UploadField` 封装视频选择与拖拽上传，`FieldSelect` 封装下拉菜单，`RangeInput` 使用 Radix Slider 处理拖动与键盘操作。

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

Bilibili、`OPENAI_*`、`SUMMARY_MAX_INPUT_CHARS` 和 `SITE_URL` 均可在管理后台保存；以下环境变量保留为部署默认值或旧部署的初始化来源。后台已保存的字段优先于环境变量。

- `DATA_DIR`：运行时数据目录，默认 `/data`。
- `FRONTEND_DIST`：前端静态文件目录，镜像内默认 `/app/frontend/dist`。
- `FONT_FILE`：FFmpeg `drawtext` 使用的字体文件路径。
- `/data/fonts`：页面可扫描的字体目录，用于文字层字体选择和预览。
- `CORS_ORIGINS`：开发时允许的跨域来源，默认 `*`。
- `BILIBILI_COOKIES_FILE`：Netscape 格式 Bilibili cookies 文件路径，推荐挂载到 `/data/cookies/bilibili.cookies.txt`。
- `BILIBILI_COOKIE_HEADER`：浏览器请求里的原始 `Cookie` header，例如 `SESSDATA=...; bili_jct=...`。
- `BILIBILI_COOKIES`：Netscape cookies 文件内容，适合通过部署平台 Secret 注入。
- `BILIBILI_REFRESH_TOKEN`：Web 登录刷新令牌，对应同一登录的 localStorage `ac_time_value`；自动续期所需，刷新后优先使用持久化的新令牌。
- `BILIBILI_KEEPALIVE_ENABLED`：自动检查和续期，默认 `true`；设为 `false` 可关闭后台及请求前的检查。
- `BILIBILI_CHECK_INTERVAL_SECONDS`：登录态检查间隔，默认 `86400`（24 小时），最小 `3600`；启动及业务请求共用检查结果。
- `BILIBILI_RETRY_INTERVAL_SECONDS`：网络或续期临时失败后的重试间隔，默认 `300`，最小 `60`。
- `OPENAI_API_KEY`：大模型 API Key 的可选初始值；可直接在后台填写，未配置有效密钥时总结页会返回 503。
- `OPENAI_BASE_URL`：大模型 OpenAI 兼容接口地址，默认 `https://api.deepseek.com`。
- `OPENAI_MODEL`：模型名，默认 `deepseek-chat`。
- `OPENAI_TIMEOUT`：大模型响应读取等待超时（秒），默认 `300`；连接和连接池等待为 `10` 秒，发送请求为 `30` 秒。SDK 对超时、连接错误及部分 HTTP 错误最多自动重试 `2` 次，整个调用耗时可能超过此设置。
- `OPENAI_REASONING_EFFORT`：可选思考深度。未配置时，`glm-5.3` 系列自动使用 `low`；其他模型保持服务商默认值。可按模型能力设为 `low`、`high`、`max`。
- `OPENAI_THINKING_TYPE`：可选 `enabled` / `disabled`，空值保留服务商默认。仅用于支持该参数的模型。GLM-5.3 系列不能关闭思考，设置 `disabled` 时会提示改用 `low` 或切换模型。
- `OPENAI_REASONING_MAX_TOKENS`：GLM-5.3 系列首次调用的输出 token 预算下限，默认 `16384`，为思考与正文共同预留空间；截断修正时最多提高到该值的两倍。预算是上限，实际使用量以响应 usage 为准。
- `OPENAI_JSON_MODE`：练习题流程默认请求 `json_object` 输出；设为 `false` 可关闭。服务商明确拒绝 JSON 模式时自动回退，同一模型后续请求复用回退结果。
- `SUMMARY_MAX_INPUT_CHARS`：总结分段时每段的字幕字符预算，默认 `9000`（越长越完整，但更慢更费 token）。

出题及复核输出比普通视频总结长，默认给模型 `300` 秒的响应读取等待时间。可在后台「大模型连接」将响应等待时间调整为 `300` 秒，保存后生效。尚未通过后台保存时，也可将 `.env` 的 `OPENAI_TIMEOUT=60` 改成 `OPENAI_TIMEOUT=300`，并执行 `docker compose up -d --force-recreate app` 应用环境变量；单纯 `restart` 不会更新环境变量。

LLM 日志包含本次调用标识、读取超时、最大重试次数、总耗时和错误类型：`APITimeoutError` 表示超时，`status=429` 表示限流或额度问题，`status=5xx` 表示上游服务错误。`LLM output validation failed; regenerating once` 表示返回的 JSON / 题目结果不符合要求后重新生成，与网络重试不同；正常出题后的答案复核也会再调用一次模型。

`finish_reason=length` 表示输出达到 token 上限，练习题流程不会接受该次结果；修正请求携带上次返回内容，提高 token 预算并要求压缩描述、重新输出完整 JSON，最多修正一次。日志还会显示 `reasoning_chars`、`completion_tokens`、`reasoning_effort` 和 `thinking`，便于区分思考耗时、正文输出与截断。知识点提炼失败后点击重试会复用已完成的转写。

GLM-5.3-Flash 只能启用思考，可以用 `low` 降低深度；GLM-4.7 系列支持非思考模式，具体以[模型说明](https://docs.z.ai/guides/llm/glm-4.7)和[接口参数](https://docs.z.ai/api-reference/llm/chat-completion)为准。若希望使用 GLM-4.7-Flash 的非思考模式，可在包含这些配置的新版本中设置：

```dotenv
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OPENAI_MODEL=glm-4.7-flash
OPENAI_THINKING_TYPE=disabled
OPENAI_REASONING_EFFORT=
```

也可在管理后台设置上述服务地址、模型和思考模式，保留现有智谱 API Key 后保存，无需重新创建容器。如果使用环境变量，执行 `docker compose up -d --force-recreate app`。这些配置需要包含对应代码的新镜像支持。

## 运行时依赖

镜像内置：

- Python 3.12
- FFmpeg
- yt-dlp
- DejaVu 与 Noto 中文字体
- Pandoc（Word 原生公式导出）
- SenseVoice-Small / Qwen3-ASR-0.6B（本地中文语音识别），保留 faster-whisper 兼容模式

Bilibili 下载取决于部署环境网络可达性和 cookie 有效性。部分视频如果需要会员、地区权限或 cookie 过期，yt-dlp 可能无法下载，后端会把失败原因返回给前端。

## 讲课视频转练习题

`/#/exercises` 支持上传讲课视频，或输入 B 站 BV / URL 并选择单个分 P。下载或上传完成后，在服务端通过 `.env` 的 `ASR_BACKEND` 选择 **SenseVoice-Small** 或 **Qwen3-ASR-0.6B** 识别中文语音，再使用现有 `OPENAI_*` 配置提炼知识点。默认 SenseVoice-Small，保留 Whisper 兼容模式。页面只显示服务端配置的模型。修改 `.env` 后重新创建容器并刷新页面，新识别任务使用新模型。

1. 查看带时间戳的转写，校对术语、补充没有念出的板书公式；保存后重新提炼知识点。
2. 编辑知识点并保存，勾选出题范围。
3. 选择单选、填空、简答、计算题，设置基础 / 巩固 / 提高难度及生成数量（1–30，默认 10）。
4. 每批题目生成后自动保存为独立试题页面。可以直接查看完整试题，也可以勾选题目、修改标题后点击 **保存所选题目并打开页面**。
5. 在试题页面切换 **练习卷** / **答案解析**，分别点击打印；也可以通过浏览器打印窗口保存为 PDF。

试题地址形如 `/#/exercises/papers/er-ci-fang-cheng`，根据题目关联的知识点转为拼音；同名试题使用 `-2`、`-3` 等序号区分。首页显示最近生成的试题，出题工作台提供可翻页的全部记录；点击标题即可回看、复制地址和打印。每个页面保留题目及生成时知识点的独立快照，课程修改、重新出题或原视频缓存过期不会改变已经保存的页面。已有题目批次会在服务启动时自动补入记录，重复保存相同标题和选题会复用页面地址。

练习卷与答案解析按相同顺序连续编号，使用 A4 黑白排版；练习卷保留答题空间，打印时隐藏导航、页面地址与操作按钮。网页支持 Markdown 与 LaTeX，包括分式、根号、上下标、积分、求和、矩阵与分段函数。使用 `$...$` / `$$...$$`，不支持自定义宏或 LaTeX 绘图。候选题只预览和勾选，不提供题目编辑器；可以重新生成独立批次，旧题目及其知识点版本会保留。

长视频按完整转写分段提炼，不截掉后半段。只分析语音文字，不自动识别 PPT 或板书画面；可手动补充。题数少于所选知识点时，从选定范围均匀取点。出题跨知识点合并，每批最多 5 道，并逐批复核答案和解析；普通 10 道题共 4 次大模型调用（不计校验修正或接口重试），原文过长时会自动拆成更小批次。本次出题任务的后续批次和复核共用对话 history，携带此前返回的完整题目、答案及解析；复核直接读取上一条 assistant 消息，避免再次拼接题目正文。跨批重复题、题型顺序或知识点分配不符会触发一次修正。history 会随请求作为输入发送，输入计费以服务商规则为准。后台日志显示计划批次数、调用次数及每次请求的 history 消息数。仍建议在打印前查看候选题。

转写和出题为后台任务，页面显示阶段及进度，刷新后恢复最近课程；页面地址中的 `lesson` 参数也可以重新打开该课程。错误可重试，同一模型已完成的转写会复用。切换模型后，点击“使用新模型重新识别”，会重新转写并提炼知识点，更新内容版本，保留旧题目批次；会替换当前转写及手动修改，需原视频文件存在。任务使用提交时的模型，运行中不可切换。切换识别失败时保留旧内容；识别成功但知识点提炼失败时，重试复用新模型的转写。课程文字、知识点、题目批次与试题页面保存在 `/data/exercises/exercises.db`。课程关联的上传视频及 B 站视频随课程保留，服务重启或超过 24 小时不访问均不会自动清理；其他原视频缓存仍在 24 小时未使用后清理，处理中的原视频也不会被清理。服务重启会把未完成任务标记为中断，可手动重试。

部署时同时持久挂载 `/data/uploads`、`/data/downloads` 和 `/data/exercises`（Compose 已配置）。视频元数据中的文件路径按当前数据目录解析，兼容旧的绝对路径记录。升级不会恢复此前已被清理的视频文件；这类视频需要重新上传。

环境变量（Compose 已传入）：

- `ASR_BACKEND`：`sensevoice`（默认）、`qwen3` 或 `whisper`，所有新识别任务采用此配置；页面不能覆盖服务端选择。
- `ASR_MODEL_SOURCE`：新模型下载源，默认 `modelscope`，也支持 `huggingface`。
- `SENSEVOICE_MODEL`：默认 `iic/SenseVoiceSmall`，可传本地模型目录。
- `QWEN_ASR_MODEL`：默认 `Qwen/Qwen3-ASR-0.6B-hf`，可传本地模型目录。使用官方 Transformers 原生 `-hf` 权重（同为 0.6B），本地目录也需对应此版本。
- `ASR_VAD_MODEL`：默认 `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch`，两个中文模型共用，可传本地目录。
- `ASR_MODEL_DIR`：新模型缓存根目录，默认 `DATA_DIR/models`；ModelScope、Hugging Face 缓存分开保存。
- `ASR_DEVICE`：新模型推理设备，默认 `cpu`；本地支持 PyTorch 的设备如 `cuda:0`、`mps`。默认 Docker 镜像安装 CPU 版 PyTorch，GPU 部署需自行安装匹配运行时。
- `ASR_CPU_THREADS`：新模型 CPU 线程数，默认 `4`，所有转写串行执行，每次只保留一个识别模型以控制内存。
- `ASR_SEGMENT_SECONDS`：语音段最长秒数，默认 `25`，范围 `5–30`。SenseVoice / Qwen 时间戳来自 VAD 的原视频语音段位置，适合内容回看，不是逐字强制对齐。
- `WHISPER_MODEL`：兼容模式模型，默认 `small`；也可传本地 CTranslate2 模型目录。
- `HF_ENDPOINT`：Hugging Face 下载地址，默认 `https://hf-mirror.com`；可设为 `https://huggingface.co` 使用官方源。不影响 ModelScope 下载。
- `HF_HUB_DISABLE_XET`：默认 `1`，关闭 Xet/CAS 文件重建，避免镜像下载仍访问 `cas-server.xethub.hf.co` 而报 401。需要在 Python 进程启动前设置；官方网络下可显式设为 `0`。
- `WHISPER_CPU_THREADS` / `WHISPER_MODEL_DIR`：兼容模式线程数 / 缓存目录，默认 `4` / `DATA_DIR/models`。
- `EXERCISE_MAX_INPUT_CHARS`：知识点提炼每段原文预算，默认 `9000`。

两个中文模型均使用本地推理，无需外部转写 API。Qwen 在 CPU 上通常比 SenseVoice 慢，也需要更多内存；首次使用下载对应识别模型和小型 VAD 模型，后续复用持久缓存。长视频先用 FFmpeg 提取 16 kHz 单声道音轨，再按 VAD 片段识别，保留原视频时间位置。知识点与出题需要有效的 `OPENAI_API_KEY`。

切换示例（已运行包含新模型依赖的镜像时）：

```dotenv
ASR_BACKEND=qwen3
ASR_MODEL_SOURCE=modelscope
HF_HUB_DISABLE_XET=1
```

保存 `.env` 后执行 `docker compose up -d --force-recreate app`，然后刷新页面。切回 SenseVoice 将 `ASR_BACKEND` 改为 `sensevoice`。环境变量固定在任务提交时，服务重启前正在进行的任务会被标记为中断，可重试。

Docker 镜像包含新模型依赖、CPU PyTorch、Pandoc 和 Noto 中文字体。Compose 持久挂载 `./data/exercises` 与 `./data/models`。模型切换的代码变更需要使用包含本次更新的新镜像；本地从源码构建可运行：

```bash
docker build -t video2any:local .
# 将 docker-compose.yml 的 image 改为 video2any:local 后：
docker compose up -d --force-recreate app
```

若旧版本 Whisper 下载出现 `File reconstruction error / CAS Client Error / 401 Unauthorized`，可先在 `.env` 增加 `HF_HUB_DISABLE_XET=1`，并在 Compose 的 `environment` 中增加 `HF_HUB_DISABLE_XET: ${HF_HUB_DISABLE_XET:-1}`，然后运行 `docker compose up -d --force-recreate app` 再重试。此变量由 Hugging Face 官方支持：[环境变量说明](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables#hfhubdisablexet)。只修改 `.env` 而不传入容器不会生效；`docker compose restart` 不会应用新的环境变量。已有正常模型缓存无需删除。

若提示 `Network is unreachable`，检查容器到下载源的网络。新模型默认使用 ModelScope；可改为 `ASR_MODEL_SOURCE=huggingface` 并配置 `HF_ENDPOINT`，或者配置可达的代理。离线部署需预先准备识别模型和 VAD 模型完整目录，放到 `./data/models` 下，并使用容器路径，例如：

```dotenv
ASR_BACKEND=sensevoice
SENSEVOICE_MODEL=/data/models/SenseVoiceSmall
QWEN_ASR_MODEL=/data/models/Qwen3-ASR-0.6B
ASR_VAD_MODEL=/data/models/fsmn-vad
HF_HUB_DISABLE_XET=1
```

本地开发建议 Python 3.12，安装 `backend/requirements.txt`。本地 `.env` 不会自动读取，启动时传入环境变量，例如 `ASR_BACKEND=qwen3 HF_HUB_DISABLE_XET=1 uvicorn backend.app.main:app --reload`。

兼容旧版 Word 导出接口还需安装 Pandoc 和 Noto Serif CJK SC 字体（Linux 可安装 `pandoc fonts-noto-cjk`，macOS 可使用 Homebrew 安装 Pandoc 并安装 Noto 中文字体）。请保持单个 Uvicorn worker；本模块的工作池及清理保护由单进程管理。

验证：

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest backend/tests
cd frontend
npm run build
```

Word 集成测试需要 Pandoc；也可在测试环境安装 `pypandoc_binary`，不影响生产依赖。
