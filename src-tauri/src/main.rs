#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod i18n;
mod platform;
use i18n::text;

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::{
    atomic::{AtomicBool, AtomicU64, Ordering},
    Arc, Mutex,
};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::{
    menu::{Menu, MenuEvent, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, RunEvent,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

/// The launcher is intentionally a menu-bar-only process. The actual UI is
/// injected into Codex by the sidecar, so this app must never create a second
/// Taskboard window.
struct SidecarState {
    child: Mutex<Option<ManagedChild>>,
    data_directory: PathBuf,
    control_path: PathBuf,
    log_path: PathBuf,
    log_file: Mutex<File>,
    generation: AtomicU64,
    status_item: Mutex<Option<MenuItem<tauri::Wry>>>,
    status: Mutex<ServiceStatus>,
    localized_items: Mutex<Vec<(MenuItem<tauri::Wry>, &'static str, &'static str)>>,
}

struct ManagedChild {
    process: CommandChild,
    exited: Arc<AtomicBool>,
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
        "stopped" => Some((ServiceStatus::Stopped, message.unwrap_or_default())),
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
    exited: Arc<AtomicBool>,
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
                    exited.store(true, Ordering::SeqCst);
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

fn start_sidecar(app: &AppHandle, state: &Arc<SidecarState>) -> Result<(), String> {
    if state
        .child
        .lock()
        .map_err(|_| "sidecar state lock is poisoned".to_string())?
        .is_some()
    {
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
        .env("CODEX_TASKBOARD_LANGUAGE", text("zh-CN", "en"))
        .args([
            "--data-dir".to_string(),
            state.data_directory.to_string_lossy().into_owned(),
            "--control-file".to_string(),
            state.control_path.to_string_lossy().into_owned(),
        ]);
    let exited = Arc::new(AtomicBool::new(false));
    let (events, child) = command.spawn().map_err(|error| error.to_string())?;
    append_log(state, "lifecycle", &format!("sidecar started with pid {}", child.pid()));
    state
        .child
        .lock()
        .map_err(|_| "sidecar state lock is poisoned".to_string())?
        .replace(ManagedChild { process: child, exited: Arc::clone(&exited) });
    observe_sidecar_events(app.clone(), Arc::clone(state), events, generation, exited);
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
        let deadline = Instant::now() + Duration::from_secs(12);
        while !child.exited.load(Ordering::SeqCst) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(100));
        }
        if !child.exited.load(Ordering::SeqCst) {
            append_log(state, "lifecycle", "sidecar shutdown timed out; terminating owned sidecar processes");
            platform::terminate_sidecar_children(child.process.pid());
            let _ = child.process.kill();
        }
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
        platform::allow_foreground_activation();
        send_control(state, "open")?;
        append_log(state, "menu", "requested Taskboard open in Codex");
        return Ok(());
    }
    drop(child);
    start_sidecar(app, state)
}

fn view_log(app: &AppHandle, state: &Arc<SidecarState>) -> Result<(), String> {
    // The existing shell plugin delegates to LaunchServices / Windows Shell.
    #[allow(deprecated)]
    app.shell().open(state.log_path.to_string_lossy().into_owned(), None)
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
            if let Err(error) = view_log(app, &state) {
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
            platform::configure(app.handle())?;
            let data_directory = platform::data_directory(app.handle());
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
            append_log(&state, "lifecycle", "tray launcher started");
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
                .icon(platform::tray_icon())
                .icon_as_template(cfg!(target_os = "macos"))
                .tooltip("Codex Taskboard")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(handle_menu_event)
                .build(app)?;

            if let Err(error) = start_sidecar(&app.handle(), &state) {
                append_log(&state, "lifecycle", &error);
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
