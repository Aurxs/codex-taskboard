<div align="center">
  <img src="src-tauri/icons/icon.png" width="112" alt="Codex Taskboard 应用图标" />
  <h1>Codex Taskboard</h1>
  <p><strong>把任务排进看板，让 Codex 接着做。</strong></p>
  <p>本地优先 · Codex 内嵌看板 · 任务依赖 · 自动调度 · 人工确认</p>
  <p>
    <img src="https://img.shields.io/badge/platform-macOS_Apple_Silicon_%7C_Windows_x64-black" alt="macOS Apple Silicon | Windows x64" />
    <img src="https://img.shields.io/badge/version-0.1.0-blue" alt="版本 0.1.0" />
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green" alt="Apache-2.0" /></a>
  </p>
  <p><a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a></p>
</div>

Codex Taskboard 是一个运行在 Codex 内部的本地任务看板：整理需求、设置前置依赖，再交给 Codex 执行。菜单栏启动器负责启动服务和挂载面板，任务、执行记录与项目设置保存在本机 SQLite 数据库中。

这是一个独立社区项目，与 OpenAI 无隶属或背书关系。

![四列任务看板](docs/images/project-ui.png)

> 首图为当前版本重新截取的四列看板，其余图片展示任务创建和详情布局；示例任务及模型选项不代表运行时数据，可用模型以本机 Codex 返回结果为准。

## 功能亮点

| 功能 | 使用方式 |
| --- | --- |
| 自定义看板 | 等待认领、处理中始终显示；等你确认、已完成、已取消可在“显示选项卡”中开关，已取消固定在最右侧；显示偏好在本机保存 |
| 任务依赖 | 指定前置任务，阻塞解除后再进入执行流程 |
| 并行调度 | 普通任务可选择允许并行；任务组按依赖并行执行子任务，不设并发数量上限 |
| 自动化开关 | 自动认领默认关闭，人工审阅默认开启 |
| 额度续跑 | 默认开启额度自动续跑，也可按项目关闭并手动恢复原 thread |
| 执行选项 | 默认沿用 Codex 会话，也可为任务选择模型和推理强度 |
| 本地集成 | 从 Codex 宿主同步项目和工作目录，使用 App Server 执行任务 |

### 创建任务，把上下文交代清楚

填写标题、Markdown 描述、优先级和验收要求。常用选项在紧凑工具栏中；点击「更多」搜索和编辑模型、推理强度、执行位置、分支和修改范围。编辑页与列表在同一浮层内切换，关闭浮层保留表单草稿；详情中修改后点击「保存设置」。

创建时可附加 PNG、JPEG、GIF、WebP 图片及 Markdown 文档，支持移除待上传文件；最多 10 个附件，单个最多 10 MB、合计最多 20 MB。附件随任务保存，在详情中下载，执行时通过本地文件路径提供给 Codex。

尚未构思完成的任务可选择「草稿」优先级。草稿保存在待认领列表，不会自动认领或直接运行；将优先级改为其他等级后即可发布。「暂停并退回草稿」会中断当前回合并保留原会话，暂停后不会被自动认领。

![创建任务与执行选项](docs/images/task-controls.png)

### 设置依赖，让执行顺序更清晰

在「阻塞于」中选择前置任务，拆分有先后关系的工作。

![选择前置任务](docs/images/dependency-open.png)

### 在详情中查看状态与执行记录

集中查看任务属性、执行阶段、依赖关系和运行记录，也可以手动交给 Codex 或取消任务。

普通任务和子任务在执行中、等你确认和已完成时，详情左侧底部提供跟进输入框；父任务组通过选择具体子任务跟进。执行中发送的文字会补充到当前回合，结束后会继续原会话；Enter 发送，Shift+Enter 换行，发送失败保留输入。长描述编辑和执行记录可以独立滚动。点击「在 Codex 中打开」可进入原生对话，在 Codex 中发送的用户消息、后续回复和状态也会同步回来。

常规启动与开发模式共用 Codex 桌面已有的会话服务，不伪造原生通知、不修改原生输入框或会话界面。实时事件同步之外，每 5 秒核对最新回合，弥补重连时遗漏的事件。升级后需重新启动启动器以加载新代码；`--backend-only` / `--no-injector` 诊断模式仍使用独立 stdio 服务，不提供原生双向同步。

通过桌面连接启动或追加任务回合时，Taskboard 会先注册该会话的原生浏览器路由，让任务无需先打开原生对话即可调用已启用的内置浏览器。浏览器插件、网站权限和审批仍由 Codex 管理；独立 stdio 诊断模式不提供这项桌面浏览器集成。

![任务详情](docs/images/task-detail.png)

普通独占任务的执行设置支持「当前项目目录」或「新工作树」。新工作树复用 Codex 桌面的原生创建、归属和清理能力，需要桌面连接；可填写已有本地或远程起始分支（例如 `main` 或 `origin/main`），留空则复制当前工作区状态。分支作为新工作树的起点，由 Codex 管理独立检出。启动后锁定执行位置和分支，暂停、重试和后续反馈沿用已保存的工作树与会话。详情中显示实际工作树路径。创建超时或连接中断时，请先在 Codex 中检查是否已创建工作树。

### 普通任务并行与任务组

普通任务默认「独占执行」。首次启动前选择「允许并行」后，系统自动使用独立 Codex 工作树和会话；所有就绪任务都可执行，没有“两任务上限”或并发数量配置。同一 Git 仓库的不同项目映射共享独占与范围约束。排到前面的就绪独占任务会等待现有执行退出，并阻止后面的并行任务插队；前置依赖未就绪的独占任务不阻塞其他就绪任务。

选择「并行任务组」并创建后，在详情逐个添加子任务、声明依赖与修改范围，或点击「AI 拆分」生成可编辑草案。只有确认草案才会写入子任务；至少一个有效子任务后才可「提交执行」。自动认领关闭时，提交后手动运行任务组。第一版支持一层子任务，子任务只依赖同组任务；跨组通过父卡设置依赖。父任务没有实施 Agent。

子任务默认继承父任务模型和推理强度，并从前置成果已集成的组内版本启动。一个子任务失败只阻塞其后继任务。所有子任务集成后，父任务整体进入审阅；点击「确认并合入」后才交付到目标分支。关闭人工审阅时自动合入，只有 Git 核验成功才完成。父卡显示已集成数量、运行数量和需处理项，看板不展开子卡。

并行任务与任务组从目标分支的已提交版本启动；可在「更多」选择其他已有起始分支。目标默认创建时的当前本地分支，不隐式复制未提交内容。首次启动后固定基线、执行位置及目标；所有合并记录绑定源提交、目标提交和结果提交。无冲突先由 Git 合并，有冲突才使用单独的 Codex 修复会话；同仓库的合并串行，实施仍可并行。目标前进时重新准备；脏目标目录需处理后重试，不自动 stash、reset 或覆盖。

修改范围每行填写一个仓库内文件或目录，不支持 glob。范围预留保留至成果合入，任务组对外预留子任务范围并集；重叠范围排队。范围预留是协作约束，不拦截任意 Shell 写入，未声明范围的任务仍可能产生冲突。实际修改越界时先暂停、确认扩大范围，再继续合并，原成果会保留。

暂停先停止新增派发，再核对原生回合已停止；未确认时显示等待原因并保留占用。暂停任务组后可新增子任务、移除未开始任务和修改其依赖。返工前置成果会标记已使用成果的下游需重新验证。取消保留已有成果与历史，不回滚已合入提交。重连会核对会话、工作树和持久化操作；创建结果不确定时通过详情关联原工作树或会话，避免重复创建。在原生 Codex 启动的冲突回合会同步为需处理项，系统不能事前拦截外部启动。

升级将旧任务保留为普通独占任务，原有工作树、会话与依赖不变，也不会追溯自动合并旧任务。

## 界面语言

Taskboard 自动跟随 Codex 的显示语言：简体中文显示中文，其余语言（包括繁体中文）统一显示英文。看板、侧栏入口和菜单栏启动器会随 Codex 的语言变化更新；启动器尚未收到 Codex 语言信息时默认使用英文。

语言切换会保留正在填写的表单和跟进草稿，不会翻译任务标题、描述、附件或会话内容。任务执行提示词会要求 Codex 使用任务作者的语言回复。

## 系统要求

- macOS 14 或更高版本，Apple Silicon（arm64）；新增 Windows 11 x64 平台实现与安装包构建，Windows 实机验收仍待完成。Linux、Intel Mac 与 Windows ARM64 尚未验证。
- 已安装并登录的 Codex 桌面客户端。
- 从源码运行：Python 3.13+、Node.js 22+ 和 npm。
- 构建 App / DMG：额外需要 Xcode Command Line Tools、Rust 与 PyInstaller；Tauri CLI 已列入 npm 开发依赖。

Windows 的安装、开发、模块边界与实机验收状态见 [Windows 使用与开发](docs/windows.zh-CN.md)。

## 快速开始

```bash
git clone https://github.com/Aurxs/codex-taskboard.git
cd codex-taskboard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci
python scripts/dev.py
```

启动后，在 Codex 左侧栏点击「任务面板」。选择项目后创建任务，手动交给 Codex，或在自动化设置中启用自动认领。保留默认人工审阅时，执行结束后由你确认结果。

如果当前 Codex 没有开放 CDP，启动器会先请求确认，再正常退出并以专用 profile 重新打开客户端。启动源码开发模式时，请保持终端运行。

## 开发

需要 Python 3.13、Node.js/npm；安装前后端依赖后，运行统一开发命令：

```bash
python3 -m pip install -e .
npm install
python3 scripts/dev.py
```

该命令会在 loopback 上启动 Vite 和共享 sidecar（FastAPI＋CDP 注入器）。Taskboard 不提供独立工作窗口：启动器会在 Codex 侧边栏加入“任务面板”入口，点击后在 Codex 主内容区显示看板。嵌入 iframe 指向 Vite `5173`，`/api` 再由 Vite proxy 到 FastAPI `47823`；sidecar 通过桌面现有消息通道使用同一个 Codex App Server，不另起会话服务。开发数据库为仓库内 `.data/`。

常用选项：

```bash
python3 scripts/dev.py --backend-only
python3 scripts/dev.py --no-injector
python3 -m injector.cdp_injector --port 9229 --no-launch
```

注入器会自动查找 `/Applications` 或 `~/Applications` 中已经安装的 ChatGPT.app/Codex.app。若现有 Codex 已开放 loopback CDP，会直接连接；若正在运行的普通 Codex 没有开放 CDP，菜单栏启动器会先提示用户确认，正常退出该实例，再以独立 profile 和专用 loopback 端口重新打开 Codex。这样用户当前看到的 Codex 就是被注入的实例，不会静默留下一个没有任务面板的旧窗口。`CODEX_TASKBOARD_CODEX_APP`、`CODEX_TASKBOARD_CODEX_PROFILE` 和 `CODEX_TASKBOARD_CDP_PORT` 仅作为开发调试覆盖项，正常使用无需填写路径或项目 key。

## 注入安全边界

`injector/cdp_injector.py` 只读取 `127.0.0.1` 的 CDP `/json/list`，并验证 WebSocket 仍然绑定到同一 loopback 端口。`injector/inject.js` 克隆 Codex 原生侧栏按钮，把 Taskboard page 挂载到 Codex 的主内容 surface，并观察 renderer 重建后重新挂载。项目名称、项目 id、真实工作目录和当前选择由 Codex renderer 的只读 host context 提供，再幂等同步到本地数据库；用户无需手工创建项目。注入器不会修改 `app.asar`、Codex 数据文件、React 模块或全局 `fetch`，也不会向 Codex turn 注入隐藏上下文。

默认会请求 renderer 范围的 CDP CSP bypass，以便 loopback iframe 在 Codex 的 CSP 下加载；不需要时可以使用 `--no-csp-bypass`。该设置不写入 Codex 文件，且只作用于当前 CDP renderer。

## Windows 开发与安装包

在 Windows 安装 Python 3.13+ x64、Node.js 22+、Git、Rust MSVC/C++ Build Tools 后，使用 PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pyinstaller httpx ruff
npm ci
.\.venv\Scripts\python.exe scripts/dev.py
# 构建 NSIS 安装器到 output/
.\.venv\Scripts\python.exe scripts/build_windows.py
```

Windows 图标位于任务栏右侧通知区域，菜单、任务面板与 macOS 共用。数据保存在 `%APPDATA%\com.codex.taskboard`。详细说明和待实机验收项目见 [Windows 使用与开发](docs/windows.zh-CN.md)。

## GitHub Actions 手动打包

打开 [Actions → Package macOS and Windows](https://github.com/Aurxs/codex-taskboard/actions/workflows/package.yml)，点击 **Run workflow**，选择分支并确认。该工作流仅手动触发，两端独立构建，复用现有打包脚本及其冻结 sidecar 冒烟检查。

成功后，在该次运行的 **Artifacts** 中下载安装包：`codex-taskboard-macos-arm64` 包含 Apple Silicon DMG，`codex-taskboard-windows-x64` 包含 NSIS 安装 EXE。产物保留 30 天。工作流无需配置签名密钥；macOS 使用 ad-hoc 签名，未经 Apple 公证，Windows 安装包未签名。

## 构建 macOS App 与 DMG

打包还需要 Rust、Tauri CLI、PyInstaller 和 Apple Silicon macOS。先安装项目依赖，再执行：

```bash
python -m pip install pyinstaller
bash scripts/build_macos.sh
```

打包及 sidecar smoke 检查成功后，最新 DMG 会移动到项目根目录的 `output/`（已由 Git 忽略），并清理 `output/` 和 Tauri DMG 暂存目录中的旧版 DMG。`.app` 仍位于 `src-tauri/target/aarch64-apple-darwin/release/bundle/macos/`。

`scripts/build_macos.sh` 会先运行 `npm run build:web`，再由 `build_sidecar.py` 校验 `dist/web/index.html` 并把整个 `dist/web` 与 `src/codex_taskboard` 收进 PyInstaller sidecar，最后执行 Tauri build。没有配置 Developer ID 时，脚本会使用完整的本地 ad-hoc 签名；该产物可用于本机测试，但没有经过 Apple 公证。打包态 sidecar 会在导入 FastAPI 前将 PyInstaller 的 `_MEIPASS/dist/web` 设置为 `CODEX_TASKBOARD_STATIC_DIR`，因此 iframe 仍然指向 loopback 的 FastAPI `47823`，不需要额外的静态文件服务器。开发运行时也会自动探测仓库根目录的 `dist/web`；如需覆盖可直接设置 `CODEX_TASKBOARD_STATIC_DIR`。

Tauri 配置在 `src-tauri/tauri.conf.json`，shell 权限在 `src-tauri/capabilities/default.json`。macOS 启动器只显示菜单栏图标，`LSUIElement` 与 `ActivationPolicy::Accessory` 共同确保它不常驻 Dock，也不会创建独立任务面板窗口。菜单可查看注入状态、在 Codex 中打开任务面板、重启服务、打开启动日志或退出。打开和停止命令通过应用支持目录中的本地 control mailbox 发送给 frozen sidecar；退出 Taskboard 不会顺带关闭用户的 Codex 窗口。未安装 Rust、Tauri CLI 或 PyInstaller 时，脚本会明确失败，不会声称已经生成 `.app`/DMG。

构建脚本最后会执行 sidecar 冒烟检查。开发者也可按需手动运行以下检查：

```bash
python3 scripts/check_packaging.py
python3 scripts/check_sidecar_smoke.py --required
```

`check_sidecar_smoke.py` 会用随机 loopback 端口启动冻结后的 sidecar，验证 `/health`，再通过同一 control mailbox 检查退出清理。最终的 Codex 重启确认、侧栏视觉效果、renderer 重建注入和卸载后数据保留仍需在 Apple Silicon macOS 图形会话中实机验证。

## 数据与隐私

- 桌面版数据默认位于 `~/Library/Application Support/Codex Taskboard/`；源码开发模式使用仓库内 `.data/`。
- 本地数据库、日志、浏览器 profile 和构建产物不上传到本仓库。
- 任务由 Codex App Server 执行；「本地优先」指看板服务和数据存储在本机，并不意味着模型离线运行。
- 部署时将三个仅显式调用的 Taskboard skills 安装到用户的 Codex 全局 skills 目录，由 Taskboard 消息显式引用；用户自行发起的普通 Codex 对话不会自动加载这些规则。不使用 Scheduled Tasks，不覆盖 system/developer 指令、沙箱或审批设置。

## Taskboard skills

执行规则收纳为三个简短 skill：`codex-taskboard-execute`（执行、跟进和重试）、`codex-taskboard-plan`（JSON 拆分草案）、`codex-taskboard-merge`（集成冲突处理）。每个 skill 都设置 `policy.allow_implicit_invocation: false`，仅在显式引用时使用。

桌面版在首次启动及升级后的部署初始化中、连接 Codex 前安装或更新；`scripts/dev.py` 和 `codex-taskboard` 启动入口也会执行幂等安装。目录为 `$CODEX_HOME/skills`，未设置时使用 `~/.codex/skills`。直接使用 Uvicorn 部署时，先运行：

```bash
python -m codex_taskboard.task_skills
```

安装仅更新 Taskboard 管理的 skill 文件；遇到已有同名但非 Taskboard 管理的目录会报错，避免覆盖用户自己的 skill。个人定制请使用其他名称，受管理文件会在下次部署启动时恢复。安装失败会阻止启动并记录错误。任务发送阶段只引用已安装文件，不安装 skill，也不修改 Codex 全局配置。

执行、拆分和合并回合通过 [App Server skill input](https://developers.openai.com/codex/app-server) 显式引用对应的 `$codex-taskboard-*` 名称和绝对路径。Python 包与桌面 sidecar 都包含这些 skill 资源。

## 架构

`src/codex_taskboard` 负责 SQLite schema/migrations、FastAPI API、SSE、调度器和 Codex 会话协议客户端。`web` 是只在 Codex 内嵌态提供完整功能的 React/Vite 看板。`injector` 提供 Python CDP 控制器、被动原生消息读取和看板宿主桥。`src-tauri` 是无窗口启动器，只管理 Python sidecar 生命周期；看板始终显示在 Codex 内部。

所有公开 API 都在本地 loopback 上提供；状态写入使用版本字段进行乐观锁检查。阻塞问题通过 `item/tool/requestUserInput` 回答；Astra 的 `agentMessage.delivery=async` 问题不暂停当前回合，使用原生问题标识和结构化跟进消息回答，支持原生会话与看板之间的回答同步。

## 任务 Plan

创建普通任务后，在详情编辑区下方点击 **Plan · 生成详细计划**（草稿也可使用）。Taskboard 创建独立会话，选择原生 Plan 模式并沿用任务模型、推理强度及 Codex 的安全配置。任务在规划期间保持待认领，自动和手动执行都会等待计划处理完毕。

问题卡片支持选项、说明和自由输入。可以多轮回答，也可以发送补充要求。完整最终计划保存在 SQLite 的 `task_operations` 中，不截断为执行摘要；确认后自动附加到该任务后续执行、跟进和重试的新回合。未确认的新方案不会覆盖已有执行计划；取消规划后恢复原有调度规则。任务要求有修改时，需要发送补充让计划同步后再确认。

计划会话与执行会话分开，重启后通过原会话恢复记录；不确定的回合启动不会自动重复发送。此功能使用本机 Codex app-server 的实验性 `collaborationMode` 协议，需要支持原生 Plan 模式的 Codex 版本。任务组的 JSON 拆分草案仍使用原来的独立入口。

## 来源与许可

本项目保留 Apache-2.0 许可。看板视觉、Codex CDP 注入和桌面打包的实现思路参考了 [chuspeeism/dashi-taskboard](https://github.com/chuspeeism/dashi-taskboard)，详见 [NOTICE](NOTICE)。

应用图标复用本机 Codex 客户端的图标资源，相关图形与商标权利属于 OpenAI，不包含在本项目 Apache-2.0 代码许可授权中。
