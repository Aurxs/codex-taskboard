//! Native shell differences only. Menus and sidecar lifecycle are shared.
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

pub fn configure(app: &AppHandle) -> tauri::Result<()> {
    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Accessory)?;
    #[cfg(not(target_os = "macos"))]
    let _ = app;
    Ok(())
}

pub fn data_directory(app: &AppHandle) -> PathBuf {
    if let Some(path) = std::env::var_os("CODEX_TASKBOARD_DATA_DIR").filter(|v| !v.is_empty()) {
        return PathBuf::from(path);
    }
    #[cfg(target_os = "macos")]
    {
        app.path().home_dir().expect("home directory should be available")
            .join("Library/Application Support/Codex Taskboard")
    }
    #[cfg(not(target_os = "macos"))]
    {
        app.path().app_data_dir().expect("app data directory should be available")
    }
}

pub fn tray_icon() -> tauri::image::Image<'static> {
    #[cfg(target_os = "macos")]
    { tauri::include_image!("icons/tray-codex.png") }
    #[cfg(not(target_os = "macos"))]
    { tauri::include_image!("icons/icon.png") }
}

pub fn allow_foreground_activation() {
    #[cfg(target_os = "windows")]
    unsafe {
        // A user clicked the tray menu; let the sidecar foreground Codex.
        windows_sys::Win32::UI::WindowsAndMessaging::AllowSetForegroundWindow(u32::MAX);
    }
}

pub fn terminate_sidecar_children(pid: u32) {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // PyInstaller onefile has a Python child with the same executable.
        // Never use taskkill /T: the user's Codex may also be a descendant.
        let script = format!(
            "$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; \
             if ($p -and $p.ExecutablePath) {{ \
             Get-CimInstance Win32_Process -Filter 'ParentProcessId = {pid}' | \
             Where-Object {{ $_.ExecutablePath -eq $p.ExecutablePath }} | \
             ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }} }}"
        );
        let _ = std::process::Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", &script])
            .creation_flags(0x08000000).output();
    }
    #[cfg(not(target_os = "windows"))]
    let _ = pid;
}
