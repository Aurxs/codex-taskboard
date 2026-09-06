# Windows 兼容性评估（历史记录）

> 下文记录适配前的评估基线。后续 Windows 平台实现与当前验收状态见 [Windows 使用与开发](windows.zh-CN.md)。

评估日期：2026-09-06。代码基线：`dab28ae`。对应任务：`CODEX-TASKBOARD-66AE0A-33`。

## 结论

**Windows 有适配可能，主要业务层可复用，但当前版本不能直接作为 Windows 应用使用。** 工作集中在 Codex 桌面集成、进程生命周期和构建发布；不是换一个打包目标就能完成，也没有证据表明需要重写整个项目。

建议先以 **Windows 11 x64、原生 Windows 项目目录、原生 Codex agent** 为验证范围。这是建议的首期范围，不是已验证的系统要求；Windows ARM64、WSL 项目及跨环境执行另行评估。

最大的前置条件是 Windows 宿主是否允许现有 CDP 注入方式，以及是否提供兼容的内部消息桥。应先完成这一项小规模实机验证，成功后再做托盘启动器和安装包。本次仅做源码与官方资料评估，没有修改程序、连接 Windows 实机或操作 Taskboard。

## 外部支持基础

- OpenAI 当前的 [Windows 桌面应用文档](https://learn.chatgpt.com/docs/windows/windows-app)确认 Windows 端支持工作树、Git、内置浏览器等核心功能，可使用原生 agent 或 WSL2。因此宿主功能存在，但这不能证明第三方注入和内部桥接口兼容。该文档当前使用“ChatGPT desktop app”名称，实机验证应记录实际应用名称、版本和安装渠道。
- [Tauri Windows 开发要求](https://v2.tauri.app/start/prerequisites/#windows)提供 Windows 工具链路径：Microsoft C++ Build Tools、WebView2 和 MSVC Rust 工具链。[Windows 安装包文档](https://v2.tauri.app/distribute/windows-installer/)支持 NSIS 安装程序及 MSI；技术栈本身不构成 macOS 限制。
- [PyInstaller 官方说明](https://pyinstaller.org/en/stable/)明确它不是跨平台交叉编译器。Windows sidecar 应在 Windows 环境单独构建，不能将现有 macOS 可执行文件改名后分发。

## 仓库中的具体适配点

以下“确定”指源码可确认的行为；“待验证”不代表已发现 Windows 故障。

| 模块 | 当前证据 | 判断与所需工作 |
| --- | --- | --- |
| 前端、API、持久化及调度 | [package.json](../package.json)、[pyproject.toml](../pyproject.toml)、[后端目录](../src/codex_taskboard)使用 React/Vite、FastAPI、SQLite，主要业务没有 macOS 平台门禁 | 复用基础较好；仍需 Windows 依赖安装和关键任务流程验证。页面依赖宿主集成，单独打开网页不等于完整可用。 |
| Codex 发现、启动和激活 | [cdp_injector.py](../injector/cdp_injector.py)的 `discover_codex_app()` 在非 Darwin 返回空；可执行文件按 `.app/Contents/MacOS` 解析；`_launch_codex()` 明确拒绝非 macOS；进程表依赖 `/bin/ps`，激活依赖 AppleScript | **确定的启动障碍。** 需按实际 Windows 安装渠道发现应用和进程，适配启动参数、单实例行为、正常退出与前台激活。仅设置 `CODEX_TASKBOARD_CODEX_APP` 不能解除平台门禁。 |
| CDP 和桌面消息桥 | 同文件经 `window.electronBridge.sendMessageFromView` 发送 `mcp-request`、工作树请求和 `browser-use-session-route-capture`；[inject.js](../injector/inject.js)依赖侧栏 DOM、宿主项目上下文 | **最高优先级待验证。** HTTP/WebSocket 传输本身不依赖 macOS，但 Windows 客户端是否开放调试参数、是否保留同样的 DOM 和内部消息协议没有实机证据，也没有从上述官方文档取得兼容承诺。 |
| Tauri 启动器 | [main.rs](../src-tauri/src/main.rs)无条件导入 `tauri::ActivationPolicy`；当前锁定的 Tauri 2.11.5 仅在 macOS 导出该类型；`view_log()`直接调用 `/usr/bin/open` | **确定的源码级编译障碍及日志打开障碍。** 调用处已有条件编译，但导入仍需处理。大部分 AppleScript/`libc::kill` 函数已有 macOS 条件编译，不应将其全部误判为 Windows 编译错误；非 macOS 重启准备目前只是 `Ok(true)` 占位，仍需实现。 |
| 开发进程管理 | [dev.py](../scripts/dev.py)使用 `start_new_session=True`、`os.killpg` 和 `SIGKILL`，直接以 `npm` 名称创建子进程 | **确定存在 Unix 生命周期依赖。** 需 Windows 子进程树退出方案，并核对 `npm.cmd` 等启动入口及带空格路径的参数传递。不能认为安装依赖后现有统一开发命令即可正常启动和退出。 |
| 数据、profile 和本地文件 | [sidecar.py](../injector/sidecar.py)非 macOS 默认使用 `~/.local/share`；[app.py](../src/codex_taskboard/app.py)冻结态默认仍是 `~/Library/Application Support`；Rust 非 macOS 已使用 `app_data_dir()` | 需统一 Windows 用户数据目录。正常 Tauri 会将 `--data-dir` 传入 sidecar，问题主要是独立启动入口的默认值不一致。目录选择 API 明确仅支持 macOS；附件外部打开已有 `os.startfile` 分支。 |
| 独立 App Server 诊断入口 | [app_server.py](../src/codex_taskboard/app_server.py)仅在 macOS 找桌面内置二进制，其他平台回退 `CODEX_BIN` 或 PATH；`CODEX_APP_SERVER_COMMAND` 使用默认 POSIX `shlex.split` | 常规桌面桥不是靠这里启动独立服务。诊断入口需核对 Windows `.exe`/命令包装入口和反斜杠、空格参数解析；`CODEX_BIN`可减少整条命令解析问题，但不等于完整适配。 |
| Git、路径与并行任务 | [git_workspace.py](../src/codex_taskboard/git_workspace.py)通过参数列表调用 Git，已有反斜杠转换和 `core.ignorecase` 处理；注入器已有盘符归一化；[desktop_server.py](../src/codex_taskboard/desktop_server.py)固定 `hostId: local`，后端直接访问工作树路径 | 具备复用基础，需核对盘符、中文/空格、大小写、CRLF、长路径和 UNC，尤其仓库身份、范围重叠和越界检查。现有处理不能当作所有路径边界均已通过的证据。WSL 路径与宿主归属不能简单替换斜杠解决。 |
| 打包与验证脚本 | [build_macos.sh](../scripts/build_macos.sh)限定 Darwin/arm64；[build_sidecar.py](../scripts/build_sidecar.py)固定 `--target-architecture arm64`、默认 Apple target、输出不带 `.exe`，检测到 Rust 时拒绝非 Darwin；[tauri.conf.json](../src-tauri/tauri.conf.json)仅配置 app/dmg 与 PNG/ICNS 图标 | **确定无法直接生成 Windows 发布包。** 需 Windows 构建入口、图标、安装目标与相应验证。`--target` 目前不足以切换实际构建架构；打包检查和默认 smoke 路径同样偏向 macOS。 |

Rust 编译判断还核对了本机 Cargo 缓存中 Tauri 2.11.5 的 `src/lib.rs`：`ActivationPolicy` 的导出受 `#[cfg(target_os = "macos")]` 限制。这是源码推导，本次没有执行 Windows 编译。

## 建议的实施顺序与通过条件

1. **先验证宿主链路。** 在隔离测试项目和 Windows 图形会话中确认实际安装位置、Codex 版本、loopback CDP、独立 profile、iframe 加载、项目目录同步，以及桌面桥的请求和事件。再手动触发一个最小任务，验证原生会话双向同步、工作树创建归属和浏览器路由。可利用现有注入器 `--no-launch` 做连接实验，但不能预先声称它在 Windows 上可用。若 CDP 或必要桥接口缺失，先停止全功能移植并重新评估集成方式。
2. **再适配系统层。** 补全 Windows 发现/启动/正常退出/激活、托盘菜单和日志打开；统一数据/profile 目录；处理开发命令与进程树清理。沿用本地 control mailbox 的退出机制，检查冻结 sidecar 子进程是否完整退出；退出 Taskboard 不应关闭用户 Codex。目录选择按现有功能需求补齐。
3. **最后打包和小范围验收。** 在 Windows 上安装项目要求的 Python 3.13+、Node.js 22+、Git 以及 Tauri/PyInstaller 构建依赖，先以 `x86_64-pc-windows-msvc` 和 NSIS 为候选。按 [Tauri sidecar 命名规则](https://v2.tauri.app/develop/sidecar/)生成 `codex-taskboard-sidecar-x86_64-pc-windows-msvc.exe`，加入 Windows 图标与配置，再验证安装、启动、重启、退出、升级后数据保留。公开分发时再决定签名和发布渠道。

独立 profile 需要单独核对登录和浏览器数据：当前实现只读复制三份 SQLite 数据库，Windows 下文件位置、锁定和加密状态是否允许复用尚未确认。允许用户在独立 profile 中重新登录，不能把数据库复制成功当成登录迁移成功。全过程保持现有 loopback 限制、宿主审批与沙箱，不通过修改客户端资源或关闭保护来获得“兼容”。

不建议把“放进 WSL”视为即刻解决方案：它不会提供 macOS 的 `.app`、`open` 和 AppleScript；桌面连接、Windows/WSL 路径、Git 所在环境和会话配置仍要对齐。官方 Windows 文档也区分了原生与 WSL2 agent。若改成独立网页加 stdio App Server，则属于另一种产品方案，会失去当前依赖桌面桥的部分能力，应另行决定。

## 验证范围及待决事项

本次完成平台相关源码和构建配置的定向检查、官方文档核对，以及文档相对链接和 Git 空白检查。仅新增评估文档及 README 入口，没有运行应用、构建、全量测试或浏览器自动化；现有测试在 macOS 上通过也不能证明 Windows 兼容。

未来实施仅需针对实际改动选择现有 `test_injector_profile.py`、`test_desktop_server.py`、`test_worktree_bridge.py`、`test_browser_bridge.py`、`test_parallel.py` 等相关用例，并补最少量平台行为测试。打包后以 `/health`、静态页面和 control mailbox 退出作为快速 smoke，再做上述 Windows 宿主验收。

评估任务已完成；**Windows 支持尚未实现或验证**。后续需要决定是否投入第一阶段实机验证，并提供目标 Windows 安装渠道/版本及可用测试环境；建议先接受 Windows 11 x64 原生目录范围，再决定 ARM64、WSL、MSI 和签名发布需求。宿主链路确认前不宜承诺适配工期或完整功能支持。
