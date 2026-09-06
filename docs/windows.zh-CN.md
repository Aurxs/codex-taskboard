# Windows 使用与开发

Windows 适配目标为 **Windows 11 x64、原生 Windows 项目与 agent**。源码已包含 Windows 平台实现、NSIS 构建和 CI；本次开发环境为 macOS，Windows 图形会话中的 Codex 注入及安装包验收仍待实机执行，不能把 macOS 上的测试通过当作 Windows 全功能验收通过。Windows ARM64、WSL agent 与跨主机项目尚未验证。

## 使用行为

启动器显示在 **Windows 任务栏右侧通知区域（系统托盘）**，可能被系统收进折叠菜单。它不创建独立任务面板窗口。菜单与 macOS 共用：状态、在 Codex 中打开任务面板、重启服务、查看日志、退出；语言仍跟随 Codex。Windows 使用完整应用图标，macOS 保留随系统主题显示的模板图标。

自动发现支持运行中的 Codex 桌面进程、注册表 App Paths、常见每用户/全局安装目录，以及当前用户的 Microsoft Store/MSIX 包清单。只有找不到安装时才需要设置 `CODEX_TASKBOARD_CODEX_APP` 为桌面 `.exe` 的完整路径；该变量不是 CLI agent 的路径。应用升级后会重新发现安装位置。

若已有实例开放 loopback CDP，直接连接；否则显示原生重启确认，确认后请求正常关闭窗口，等待退出，再用独立 profile 和 loopback 调试端口启动。用户取消或 Codex 拒绝退出时不强行结束 Codex。某些客户端关闭最后一个窗口后仍驻留后台，此时需从 Codex 自己的菜单退出后重试。退出或重启 Taskboard 只停止自身服务，保留 Codex。

目录选择使用 Windows 原生对话框；附件由系统默认应用打开。盘符根目录、UNC、空格与中文路径保留，Windows 不允许的附件落盘文件名会安全转换，面板中仍保留原名。Git 范围检查拒绝盘符逃逸、NTFS 数据流与 Git 内部目录别名。

默认数据目录：`%APPDATA%\com.codex.taskboard`，包含数据库、附件、`launcher.log`、control mailbox 和 `codex-profile`。启动器与 Python sidecar 使用同一目录。卸载/覆盖安装不自动删除用户数据；不要勾选安装器中删除应用数据的选项。开发模式使用仓库 `.data`。`CODEX_TASKBOARD_DATA_DIR`、`CODEX_TASKBOARD_CODEX_PROFILE` 可覆盖目录，覆盖时使用绝对路径。

浏览器 profile 仅尝试只读备份现有三份 SQLite 数据库，不复制或解密 Windows 加密密钥。数据库锁定或加密不能复用时仍允许启动，可能需要在独立 profile 中重新登录。`CODEX_TASKBOARD_CODEX_SOURCE_PROFILE` 可指定只读源目录。

## 开发与构建

安装 Python 3.13+ x64、Node.js 22+、Git for Windows、Rust MSVC 工具链，以及 Microsoft C++ Build Tools（桌面 C++ 工作负载和 Windows SDK）。桌面启动器需要 WebView2；NSIS 安装器会在缺少运行时时下载安装。

在项目根目录使用 PowerShell，不必更改脚本执行策略：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pyinstaller httpx ruff
npm ci
.\.venv\Scripts\python.exe scripts/dev.py
```

Ctrl+C 停止整个开发栈。`scripts/dev.py` 在两端共用；Windows 的 npm/npx/Codex npm shim 解析为 Node 脚本调用，带空格、中文或 `&` 的路径无需 shell 拼接。诊断用的 `CODEX_APP_SERVER_COMMAND` 按 Windows 参数规则解析；可优先用 `CODEX_BIN` 指向原生 agent `.exe`。自定义 `.cmd/.bat` 包装器需改成 `.exe` 或显式的 Node 脚本命令。

构建：

```powershell
.\.venv\Scripts\python.exe scripts/build_windows.py
```

脚本执行前端类型检查/构建、原生 PyInstaller sidecar、健康/静态页面/正常退出 smoke、Tauri NSIS 安装包和最终 sidecar smoke。输出为 `output\*-setup.exe`。Windows sidecar 名称为 `codex-taskboard-sidecar-x86_64-pc-windows-msvc.exe`；PyInstaller 不支持把 macOS 产物直接转换成 Windows 产物，构建脚本会拒绝平台/架构错配。

仓库的 `.github/workflows/windows.yml` 在 PR 或手动触发时运行针对性测试和上述构建，保存安装器为 artifact。本次只添加工作流，未推送或触发远程 CI。默认安装器没有代码签名；公开分发的签名证书另行配置。

## 模块边界

- `src/codex_taskboard/platforms/`：统一系统入口、用户目录、命令解析、子进程生命周期；`macos.py` / `windows.py` 仅实现系统原生 API。
- `injector/cdp_injector.py`：两端共用 CDP 发现、重启确认流程、注入、renderer 恢复、消息桥和 profile 备份。Rust 不再重复发现和重启 Codex。
- `src-tauri/src/platform.rs`：激活策略、原生图标/数据目录和 Windows 子进程收尾。`main.rs` 共用托盘菜单、语言、日志和服务控制。
- `src-tauri/tauri.conf.json`：共享配置；`tauri.macos.conf.json` / `tauri.windows.conf.json` 只覆盖安装目标和平台打包参数。
- React、API、SQLite、调度、工作树、审批和浏览器消息协议均共用。标题栏通过宿主提供的按钮几何信息避让，前端无需维护 Windows 页面副本。

新增功能应优先放入共享层；只有原生 API 不兼容时才扩展平台接口及对应实现。适配不修改 Codex 安装资源，不更改原有审批、沙箱或任务执行策略。

## 本次验证记录

在 macOS 上已通过相关 Python 测试（49 个用例）、修改范围内的 Ruff 检查、前端 TypeScript/Vite 构建、Windows 路径/标题栏几何契约、打包配置检查、`cargo check --offline`，以及重新构建的 PyInstaller sidecar `/health`、内置静态页面和 control mailbox 正常退出 smoke。Windows 原生参数解析与进程组两个用例在 macOS 跳过，交给 Windows 工作流执行。未执行无关的全量测试，也未操作真实任务或 Codex 窗口。

## 待实机验收

在 Windows 图形会话中记录 Codex 的版本与安装渠道，然后确认：安装/升级；托盘在浅深色主题中的显示；重启确认和取消；独立 profile 登录；注入、打开与 renderer 重建；目录选择；中文/空格项目的任务执行、跟进、附件和工作树；浏览器桥；停止服务后 sidecar 完全退出且 Codex 保留；卸载后数据保留。

本次尝试连接已配置的 Windows 主机时连接超时，因此未执行上述图形验收，也未生成 Windows 安装器。内部桌面桥由 Codex 客户端提供，Windows CI 的无宿主 smoke 无法替代这一项验收。

## 参考

平台配置及 NSIS 使用 [Tauri 平台配置](https://v2.tauri.app/reference/config/)与 [Windows Installer](https://v2.tauri.app/distribute/windows-installer/)；Store 发现读取 [Get-AppxPackageManifest](https://learn.microsoft.com/en-us/powershell/module/appx/get-appxpackagemanifest)；窗口按钮避让使用 [Electron Window Controls Overlay](https://www.electronjs.org/docs/latest/tutorial/custom-title-bar)。先前评估留档见 [Windows 兼容性评估](windows-compatibility.zh-CN.md)。
