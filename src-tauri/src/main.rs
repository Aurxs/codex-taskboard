#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod i18n;
use i18n::text;

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc, Mutex,
};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::{
    menu::{Menu, MenuEvent, MenuItem},
    tray::TrayIconBuilder,
    ActivationPolicy, AppHandle, Emitter, Manager, RunEvent,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

/// The launcher is intentionally a menu-bar-only process. The actual UI is
/// injected into Codex by the sidecar, so this app must never create a second
/// Taskboard window.
struct SidecarState {
    child: Mutex<Option<CommandChild>>,
    data_directory: PathBuf,
    control_path: PathBuf,
    log_path: PathBuf,
    log_file: Mutex<File>,
    generation: AtomicU64,
    status_item: Mutex<Option<MenuItem<tauri::Wry>>>,
    status: Mutex<ServiceStatus>,
    localized_items: Mutex<Vec<(MenuItem<tauri::Wry>, &'static str, &'static str)>>,
}

#[derive(Clone, Copy)]
enum ServiceStatus {
    Starting,
    Running,
    Failed,
    Stopped,
}

impl ServiceStatus {
    fn label(self) -> &'static str {
        match self {
            Self::Starting => text("启动中", "Starting"),
            Self::Running => text("已运行", "Running"),
            Self::Failed => text("失败", "Failed"),
            Self::Stopped => text("已停止", "Stopped"),
        }
    }
}

fn append_log(state: &SidecarState, stream: &str, message: &str) {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let line = message.replace('\n', "\\n");
    if let Ok(mut log_file) = state.log_file.lock() {
        let _ = writeln!(
            log_file,
            "[{}.{:03}] {stream}: {line}",
            now.as_secs(),
            now.subsec_millis()
        );
        let _ = log_file.flush();
    }
}

fn send_control(state: &SidecarState, command: &str) -> Result<(), String> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::fs::write(&state.control_path, format!("{nonce}\n{command}\n"))
        .map_err(|error| error.to_string())
}

fn data_directory(app: &AppHandle) -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        return app
            .path()
            .home_dir()
            .expect("the home directory should be available")
            .join("Library")
            .join("Application Support")
            .join("Codex Taskboard");
    }

    #[cfg(not(target_os = "macos"))]
    {
        app.path()
            .app_data_dir()
            .expect("the app data directory should be available")
    }
}

fn set_status(app: &AppHandle, state: &Arc<SidecarState>, status: ServiceStatus, message: &str) {
    *state.status.lock().unwrap() = status;
    if let Ok(status_item) = state.status_item.lock() {
        if let Some(status_item) = status_item.clone() {
            let label = format!("{}{}", text("状态：", "Status: "), status.label());
            let _ = app.run_on_main_thread(move || {
                let _ = status_item.set_text(label);
            });
        }
    }

    let _ = app.emit(
        "sidecar-status",
        serde_json::json!({
            "status": status.label(),
            "message": message,
        }),
    );
}

fn set_status_for_generation(
    app: &AppHandle,
    state: &Arc<SidecarState>,
    generation: u64,
    status: ServiceStatus,
    message: &str,
) {
    if state.generation.load(Ordering::SeqCst) == generation {
        set_status(app, state, status, message);
    }
}

fn status_from_sidecar_event(value: &serde_json::Value) -> Option<(ServiceStatus, String)> {
    let event = value.get("event")?.as_str()?;
    let message = value
        .get("message")
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned);
    match event {
        "backend_ready" => Some((
            ServiceStatus::Starting,
            message.unwrap_or_else(|| text("任务面板后端已就绪，正在连接 Codex…", "Taskboard backend is ready. Connecting to Codex…").to_string()),
        )),
        "codex_launched" => Some((
            ServiceStatus::Starting,
            message.unwrap_or_else(|| text("Codex 已启动，正在注入任务面板…", "Codex started. Loading Taskboard…").to_string()),
        )),
        "injected" => Some((
            ServiceStatus::Running,
            message.unwrap_or_else(|| text("任务面板已注入 Codex", "Taskboard is available in Codex").to_string()),
        )),
        "error" => Some((
            ServiceStatus::Failed,
            message.unwrap_or_else(|| text("任务面板服务发生错误", "Taskboard service encountered an error").to_string()),
        )),
        "terminated" => {
            let code = value.get("code").and_then(serde_json::Value::as_i64);
            if code == Some(0) {
                Some((ServiceStatus::Stopped, text("任务面板服务已停止", "Taskboard service stopped").to_string()))
            } else {
                Some((ServiceStatus::Failed, text("任务面板服务异常退出", "Taskboard service exited unexpectedly").to_string()))
            }
        }
        _ => None,
    }
}

fn consume_sidecar_line(
    app: &AppHandle,
    state: &Arc<SidecarState>,
    generation: u64,
    stream: &str,
    line: &str,
) {
    if line.is_empty() {
        return;
    }
    append_log(state, stream, line);
    let Ok(value) = serde_json::from_str::<serde_json::Value>(line) else {
        let _ = app.emit(
            "sidecar-output",
            serde_json::json!({"stream": stream, "text": line}),
        );
        return;
    };
    if value.get("event").and_then(|event| event.as_str()) == Some("language")
        && state.generation.load(Ordering::SeqCst) == generation
    {
        if let Some(language) = value.get("language").and_then(|language| language.as_str()) {
            i18n::set_language(language);
            let items = state.localized_items.lock().unwrap().clone();
            let _ = app.run_on_main_thread(move || {
                for (item, chinese, english) in items {
                    let _ = item.set_text(text(chinese, english));
                }
            });
            let status = *state.status.lock().unwrap();
            set_status(app, state, status, "");
        }
    }
    if let Some((status, message)) = status_from_sidecar_event(&value) {
        set_status_for_generation(app, state, generation, status, &message);
    }
    let _ = app.emit(
        "sidecar-output",
        serde_json::json!({"stream": stream, "text": line, "event": value}),
    );
}

fn observe_sidecar_events(
    app: AppHandle,
    state: Arc<SidecarState>,
    mut events: tauri::async_runtime::Receiver<CommandEvent>,
    generation: u64,
) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    for line in String::from_utf8_lossy(&bytes).lines() {
                        consume_sidecar_line(&app, &state, generation, "stdout", line.trim());
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    // Uvicorn and the Python injector use stderr for normal
                    // startup logs. Persist it for diagnostics, but do not
                    // infer the service state from stderr.
                    for line in String::from_utf8_lossy(&bytes).lines() {
                        consume_sidecar_line(&app, &state, generation, "stderr", line.trim());
                    }
                }
                CommandEvent::Error(error) => {
                    append_log(&state, "shell", &error);
                    set_status_for_generation(
                        &app,
                        &state,
                        generation,
                        ServiceStatus::Failed,
                        &error,
                    );
                    let _ = app.emit("sidecar-error", error);
                }
                CommandEvent::Terminated(payload) => {
                    if state.generation.load(Ordering::SeqCst) == generation {
                        if let Ok(mut child) = state.child.lock() {
                            child.take();
                        }
                        let terminated = serde_json::json!({
                            "event": "terminated",
                            "code": payload.code,
                            "signal": payload.signal,
                        });
                        if let Some((status, message)) = status_from_sidecar_event(&terminated) {
                            set_status(&app, &state, status, &message);
                        }
                        append_log(&state, "lifecycle", &terminated.to_string());
                        let _ = app.emit(
                            "sidecar-terminated",
                            terminated,
                        );
                    }
                    break;
                }
                _ => {}
            }
        }
    });
}

#[cfg(target_os = "macos")]
fn installed_codex_apps(home: &Path) -> impl Iterator<Item = PathBuf> {
    [
        PathBuf::from("/Applications/ChatGPT.app"),
        home.join("Applications/ChatGPT.app"),
        PathBuf::from("/Applications/Codex.app"),
        home.join("Applications/Codex.app"),
    ]
    .into_iter()
    .filter(|path| path.is_dir())
}

#[cfg(target_os = "macos")]
fn has_cdp_arguments(command: &str) -> bool {
    command.split_whitespace().any(|argument| {
        argument == "--remote-debugging-pipe"
            || argument == "--remote-debugging-port"
            || argument.starts_with("--remote-debugging-port=")
    })
}

#[cfg(target_os = "macos")]
fn ordinary_codex_process(app_path: &Path) -> Result<Option<u32>, String> {
    let app_name = app_path
        .file_stem()
        .ok_or_else(|| text("无法识别 Codex App 名称", "Cannot identify the Codex app name").to_string())?;
    let executable = app_path.join("Contents/MacOS").join(app_name);
    let output = StdCommand::new("/bin/ps")
        .args(["-ww", "-axo", "pid=,command="])
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(text("无法检查正在运行的 Codex", "Cannot check the running Codex app").to_string());
    }

    let executable = executable.to_string_lossy();
    let mut ordinary_pid = None;
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let line = line.trim_start();
        let Some(separator) = line.find(char::is_whitespace) else {
            continue;
        };
        let command = line[separator..].trim_start();
        if command != executable && !command.starts_with(&format!("{executable} ")) {
            continue;
        }
        if has_cdp_arguments(command) {
            return Ok(None);
        }
        ordinary_pid = line[..separator].parse().ok();
    }
    Ok(ordinary_pid)
}

#[cfg(target_os = "macos")]
fn process_is_running(pid: u32) -> bool {
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

#[cfg(target_os = "macos")]
fn confirm_codex_restart(app_path: &Path) -> bool {
    let app_name = app_path
        .file_stem()
        .and_then(|name| name.to_str())
        .unwrap_or("Codex");
    let cancel = text("取消", "Cancel");
    let restart = text("重新启动 Codex", "Restart Codex");
    let message = text(
        "需要重新启动 {app} 才能在 Codex 中显示任务面板。",
        "Restart {app} to show Taskboard in Codex.",
    ).replace("{app}", app_name);
    let script = format!(
        "display dialog \"{message}\" buttons {{\"{cancel}\", \"{restart}\"}} default button \"{restart}\" with title \"Codex Taskboard\""
    );
    StdCommand::new("/usr/bin/osascript")
        .args(["-e", &script])
        .output()
        .map(|output| {
            output.status.success()
                && String::from_utf8_lossy(&output.stdout).contains(restart)
        })
        .unwrap_or(false)
}

#[cfg(target_os = "macos")]
fn quit_codex_normally(app_path: &Path, pid: u32) -> Result<(), String> {
    let app_name = app_path
        .file_stem()
        .and_then(|name| name.to_str())
        .unwrap_or("Codex");
    let script = format!("tell application \"{app_name}\" to quit");
    let output = StdCommand::new("/usr/bin/osascript")
        .args(["-e", &script])
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(text("Codex 没有接受退出请求", "Codex did not accept the quit request").to_string());
    }
    let deadline = Instant::now() + Duration::from_secs(36);
    while process_is_running(pid) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
    }
    if process_is_running(pid) {
        return Err(text("Codex 尚未退出，任务面板没有启动", "Codex has not exited. Taskboard was not started").to_string());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn prepare_codex_for_injection() -> Result<bool, String> {
    let home = PathBuf::from(std::env::var_os("HOME").ok_or_else(|| "HOME is unavailable".to_string())?);
    for app_path in installed_codex_apps(&home) {
        if let Some(pid) = ordinary_codex_process(&app_path)? {
            if !confirm_codex_restart(&app_path) {
                return Ok(false);
            }
            quit_codex_normally(&app_path, pid)?;
            return Ok(true);
        }
    }
    Ok(true)
}

#[cfg(not(target_os = "macos"))]
fn prepare_codex_for_injection() -> Result<bool, String> {
    Ok(true)
}

fn start_sidecar(app: &AppHandle, state: &Arc<SidecarState>) -> Result<(), String> {
    if state
        .child
        .lock()
        .map_err(|_| "sidecar state lock is poisoned".to_string())?
        .is_some()
    {
        return Ok(());
    }

    if !prepare_codex_for_injection()? {
        set_status(app, state, ServiceStatus::Stopped, text("已取消重新启动 Codex，未注入任务面板", "Codex restart canceled. Taskboard was not loaded"));
        return Ok(());
    }

    let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
    std::fs::write(&state.control_path, "").map_err(|error| error.to_string())?;
    set_status(
        app,
        state,
        ServiceStatus::Starting,
        text("正在启动任务面板服务…", "Starting Taskboard service…"),
    );

    let command = app
        .shell()
        .sidecar("codex-taskboard-sidecar")
        .map_err(|error| error.to_string())?
        .args([
            "--data-dir".to_string(),
            state.data_directory.to_string_lossy().into_owned(),
            "--control-file".to_string(),
            state.control_path.to_string_lossy().into_owned(),
        ]);
    let (events, child) = command.spawn().map_err(|error| error.to_string())?;
    append_log(state, "lifecycle", &format!("sidecar started with pid {}", child.pid()));
    state
        .child
        .lock()
        .map_err(|_| "sidecar state lock is poisoned".to_string())?
        .replace(child);
    observe_sidecar_events(app.clone(), Arc::clone(state), events, generation);
    Ok(())
}

fn stop_sidecar(state: &Arc<SidecarState>) {
    // Invalidate the watcher before killing the process. Otherwise its
    // Terminated event could overwrite the state of a new process started by
    // the restart action.
    state.generation.fetch_add(1, Ordering::SeqCst);
    let child = state.child.lock().ok().and_then(|mut child| child.take());
    if let Some(child) = child {
        let _ = send_control(state, "stop");
        // The frozen one-file executable has a small supervisor process.
        // Give its Python child time to shut down Uvicorn, then reap the
        // supervisor if it has not exited by itself.
        thread::sleep(Duration::from_secs(2));
        let _ = child.kill();
    }
}

fn restart_sidecar(app: &AppHandle, state: &Arc<SidecarState>) -> Result<(), String> {
    stop_sidecar(state);
    start_sidecar(app, state)
}

fn open_taskboard_in_codex(app: &AppHandle, state: &Arc<SidecarState>) -> Result<(), String> {
    let child = state
        .child
        .lock()
        .map_err(|_| "sidecar state lock is poisoned".to_string())?;
    if child.is_some() {
        send_control(state, "open")?;
        append_log(state, "menu", "requested Taskboard open in Codex");
        return Ok(());
    }
    drop(child);
    start_sidecar(app, state)
}

fn view_log(state: &Arc<SidecarState>) -> Result<(), String> {
    StdCommand::new("/usr/bin/open")
        .arg(&state.log_path)
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

fn handle_menu_event(app: &AppHandle, event: MenuEvent) {
    let Some(state) = app.try_state::<Arc<SidecarState>>() else {
        return;
    };
    let state = Arc::clone(state.inner());
    match event.id().as_ref() {
        "open-taskboard" => {
            if let Err(error) = open_taskboard_in_codex(app, &state) {
                append_log(&state, "menu", &format!("open failed: {error}"));
                set_status(app, &state, ServiceStatus::Failed, &error);
            }
        }
        "restart-service" => {
            if let Err(error) = restart_sidecar(app, &state) {
                append_log(&state, "menu", &format!("restart failed: {error}"));
                set_status(app, &state, ServiceStatus::Failed, &error);
            }
        }
        "view-log" => {
            if let Err(error) = view_log(&state) {
                set_status(app, &state, ServiceStatus::Failed, &error);
            }
        }
        "quit" => {
            stop_sidecar(&state);
            app.exit(0);
        }
        _ => {}
    }
}

fn main() {
    tauri::Builder::default()
        .enable_macos_default_menu(false)
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.handle()
                .set_activation_policy(ActivationPolicy::Accessory)?;

            let data_directory = data_directory(&app.handle());
            std::fs::create_dir_all(&data_directory)
                .expect("the app data directory should be writable");
            let log_path = data_directory.join("launcher.log");
            let control_path = data_directory.join("launcher-control");
            let log_file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&log_path)?;

            let state = Arc::new(SidecarState {
                child: Mutex::new(None),
                data_directory,
                control_path,
                log_path,
                log_file: Mutex::new(log_file),
                generation: AtomicU64::new(0),
                status_item: Mutex::new(None),
                status: Mutex::new(ServiceStatus::Starting),
                localized_items: Mutex::new(Vec::new()),
            });
            append_log(&state, "lifecycle", "menu-bar launcher started");
            app.manage(Arc::clone(&state));

            let app_info = MenuItem::with_id(
                app,
                "app-info",
                format!("{}", app.package_info().name),
                false,
                None::<&str>,
            )?;
            let status_item = MenuItem::with_id(
                app,
                "service-status",
                text("状态：启动中", "Status: Starting"),
                false,
                None::<&str>,
            )?;
            *state.status_item.lock().unwrap() = Some(status_item.clone());
            let open_taskboard = MenuItem::with_id(
                app,
                "open-taskboard",
                text("在 Codex 中打开任务面板", "Open Taskboard in Codex"),
                true,
                None::<&str>,
            )?;
            let restart_service = MenuItem::with_id(
                app,
                "restart-service",
                text("重新启动服务", "Restart service"),
                true,
                None::<&str>,
            )?;
            let log_item = MenuItem::with_id(
                app,
                "view-log",
                text("查看启动日志", "View startup log"),
                true,
                None::<&str>,
            )?;
            let quit = MenuItem::with_id(app, "quit", text("退出", "Quit"), true, None::<&str>)?;
            *state.localized_items.lock().unwrap() = vec![
                (open_taskboard.clone(), "在 Codex 中打开任务面板", "Open Taskboard in Codex"),
                (restart_service.clone(), "重新启动服务", "Restart service"),
                (log_item.clone(), "查看启动日志", "View startup log"),
                (quit.clone(), "退出", "Quit"),
            ];
            let menu = Menu::with_items(
                app,
                &[
                    &app_info,
                    &status_item,
                    &open_taskboard,
                    &restart_service,
                    &log_item,
                    &quit,
                ],
            )?;

            TrayIconBuilder::new()
                .icon(tauri::include_image!("icons/tray-codex.png"))
                .icon_as_template(true)
                .tooltip("Codex Taskboard")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(handle_menu_event)
                .build(app)?;

            if let Err(error) = start_sidecar(&app.handle(), &state) {
                set_status(&app.handle(), &state, ServiceStatus::Failed, &error);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Codex Taskboard")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Arc<SidecarState>>() {
                    stop_sidecar(state.inner());
                }
            }
        });
}
