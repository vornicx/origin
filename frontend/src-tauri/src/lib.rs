// Origin — Native Shell (Tauri 2.x)

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use serde_json;
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{TrayIconBuilder, TrayIconEvent, MouseButton, MouseButtonState},
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, Position, RunEvent,
    Size, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_autostart::{ManagerExt as AutostartManagerExt, MacosLauncher};
use tauri_plugin_store::StoreExt;

#[cfg(target_os = "windows")]
use windows_sys::Win32::{
    Foundation::HWND,
    Graphics::Dwm::DwmSetWindowAttribute,
    UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, GWL_EXSTYLE,
        WS_EX_LAYERED, WS_EX_TOOLWINDOW,
    },
};

// DwmExtendFrameIntoClientArea isn't re-exported from windows-sys 0.59 under
// the feature set we use, so we link it manually.
#[cfg(target_os = "windows")]
#[repr(C)]
#[allow(non_snake_case)]
struct MARGINS {
    cxLeftWidth: i32,
    cxRightWidth: i32,
    cyTopHeight: i32,
    cyBottomHeight: i32,
}

#[cfg(target_os = "windows")]
extern "system" {
    fn DwmExtendFrameIntoClientArea(hwnd: HWND, pMarInset: *const MARGINS) -> i32;
}

// DWM attribute constants (Windows 11)
#[cfg(target_os = "windows")]
const DWMWA_USE_IMMERSIVE_DARK_MODE: u32 = 20;
#[cfg(target_os = "windows")]
const DWMWA_SYSTEMBACKDROP_TYPE: u32 = 38;

// ─── State ────────────────────────────────────────────────────

#[derive(Default)]
struct AppState {
    backend_ready: Mutex<bool>,
    click_through: Mutex<bool>,
    window_mode: Mutex<String>, // "orb" | "integrated" | "hud"
}

// ─── Helpers ──────────────────────────────────────────────────

fn is_origin_root(p: &std::path::Path) -> bool {
    p.join("api").join("main.py").exists()
}

fn origin_root(app: Option<&AppHandle>) -> PathBuf {
    // Resolution order:
    //   1. tauri-plugin-store "origin_root_path" (persisted user choice)
    //   2. env var ORIGIN_ROOT (set by start.bat in dev)
    //   3. Walk up from current_exe() looking for api/main.py (works for
    //      dev: target/release/origin.exe and for installed builds that
    //      sit alongside the repo)
    //   4. Current directory (last resort)
    if let Some(handle) = app {
        if let Ok(store) = handle.store("settings.json") {
            if let Some(serde_json::Value::String(s)) = store.get("origin_root_path") {
                let p = PathBuf::from(s);
                if is_origin_root(&p) {
                    return p;
                }
            }
        }
    }
    if let Ok(v) = std::env::var("ORIGIN_ROOT") {
        let p = PathBuf::from(v);
        if is_origin_root(&p) {
            return p;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..6 {
            if let Some(c) = cur.clone() {
                if is_origin_root(&c) {
                    return c;
                }
                cur = c.parent().map(|p| p.to_path_buf());
            } else {
                break;
            }
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn python_executable(app: Option<&AppHandle>) -> PathBuf {
    let root = origin_root(app);
    let venv_pythonw = root.join("venv").join("Scripts").join("pythonw.exe");
    if python_is_usable(&venv_pythonw) {
        return venv_pythonw;
    }
    let venv_python = root.join("venv").join("Scripts").join("python.exe");
    if python_is_usable(&venv_python) {
        return venv_python;
    }
    let uv_pythonw = root
        .join(".uv-python")
        .join("cpython-3.11.15-windows-x86_64-none")
        .join("pythonw.exe");
    if python_is_usable(&uv_pythonw) {
        return uv_pythonw;
    }
    let uv_python = root
        .join(".uv-python")
        .join("cpython-3.11.15-windows-x86_64-none")
        .join("python.exe");
    if python_is_usable(&uv_python) {
        return uv_python;
    }
    PathBuf::from("pythonw.exe")
}

fn python_is_usable(path: &std::path::Path) -> bool {
    if !path.exists() {
        return false;
    }
    std::process::Command::new(path)
        .arg("--version")
        .creation_flags_no_window()
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false)
}

trait CommandNoWindow {
    fn creation_flags_no_window(&mut self) -> &mut Self;
}

impl CommandNoWindow for std::process::Command {
    #[cfg(target_os = "windows")]
    fn creation_flags_no_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(0x08000000)
    }

    #[cfg(not(target_os = "windows"))]
    fn creation_flags_no_window(&mut self) -> &mut Self {
        self
    }
}

// ─── Mica / Win11 integration ─────────────────────────────────

/// Apply Windows 11 Mica backdrop + dark mode to a window handle.
/// Falls back gracefully on older Windows versions.
#[cfg(target_os = "windows")]
fn apply_mica_backdrop(hwnd: HWND) {
    unsafe {
        // 1. Enable dark mode for the caption / frame
        let dark_mode: i32 = 1;
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            &raw const dark_mode as *const _,
            std::mem::size_of::<i32>() as u32,
        );

        // 2. Set Mica backdrop (2 = Mica, 3 = Acrylic, 4 = Mica Alt / Tabbed)
        let backdrop_type: i32 = 2;
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            &raw const backdrop_type as *const _,
            std::mem::size_of::<i32>() as u32,
        );

        // 3. Extend frame into client area (required for Mica to bleed into content)
        let margins = MARGINS {
            cxLeftWidth: -1,
            cxRightWidth: -1,
            cyTopHeight: -1,
            cyBottomHeight: -1,
        };
        DwmExtendFrameIntoClientArea(hwnd, &raw const margins);
    }
}

// ─── Window helpers ───────────────────────────────────────────

const ORB_W: u32 = 480;
const ORB_H: u32 = 480;
const FULL_W: u32 = 980;
const FULL_H: u32 = 680;
const INTEGRATED_W: u32 = 1200;
const INTEGRATED_H: u32 = 820;

fn position_at_corner(window: &tauri::WebviewWindow) {
    let monitor = match window.primary_monitor() {
        Ok(Some(m)) => m,
        _ => return,
    };
    let screen = monitor.size();
    let margin_right: i32 = 24;
    let margin_bottom: i32 = 68; // above Windows taskbar (~48px) + padding
    let x = screen.width as i32 - ORB_W as i32 - margin_right;
    let y = screen.height as i32 - ORB_H as i32 - margin_bottom;
    let _ = window.set_position(Position::Physical(PhysicalPosition { x, y }));
}

// ─── Tauri Commands (invoke from React) ───────────────────────

#[tauri::command]
fn backend_ready(state: tauri::State<AppState>) -> bool {
    *state.backend_ready.lock().unwrap()
}

#[tauri::command]
fn set_click_through(window: tauri::WebviewWindow, state: tauri::State<AppState>, enabled: bool) -> Result<(), String> {
    *state.click_through.lock().unwrap() = enabled;
    window
        .set_ignore_cursor_events(enabled)
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn set_window_mode(
    app: AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<AppState>,
    mode: String,
) -> Result<(), String> {
    *state.window_mode.lock().unwrap() = mode.clone();
    if let Ok(store) = app.store("settings.json") {
        store.set("window_mode", serde_json::json!(mode));
        let _ = store.save();
    }
    match mode.as_str() {
        "orb" => {
            let _ = window.set_fullscreen(false);
            let _ = window.set_always_on_top(true);
            let _ = window.set_size(Size::Physical(PhysicalSize { width: ORB_W, height: ORB_H }));
            position_at_corner(&window);
        }
        "full" => {
            let _ = window.set_fullscreen(false);
            let _ = window.set_always_on_top(false);
            let _ = window.set_size(Size::Physical(PhysicalSize { width: FULL_W, height: FULL_H }));
            let _ = window.center();
            let _ = window.set_focus();
        }
        "integrated" => {
            let _ = window.set_fullscreen(false);
            let _ = window.set_always_on_top(false);
            let _ = window.set_size(Size::Physical(PhysicalSize {
                width: INTEGRATED_W,
                height: INTEGRATED_H,
            }));
            let _ = window.center();
            let _ = window.set_focus();
        }
        "hud" => {
            let _ = window.set_fullscreen(false);
            let _ = window.set_always_on_top(false);
            if let Ok(Some(monitor)) = window.primary_monitor() {
                let screen = monitor.size();
                let position = monitor.position();
                let _ = window.set_size(Size::Physical(PhysicalSize {
                    width: screen.width,
                    height: screen.height,
                }));
                let _ = window.set_position(Position::Physical(PhysicalPosition {
                    x: position.x,
                    y: position.y,
                }));
            } else {
                let _ = window.set_size(Size::Physical(PhysicalSize { width: 1280, height: 800 }));
                let _ = window.center();
            }
            let _ = window.set_focus();
        }
        _ => return Err(format!("Unknown window mode: {}", mode)),
    }
    Ok(())
}

/// Apply a DWM backdrop type to the window.  Accepted values: "mica", "acrylic",
/// "tabbed", "none".  Falls back to "none" for unrecognised strings.
#[cfg(target_os = "windows")]
#[tauri::command]
fn apply_backdrop(app: AppHandle, window: tauri::WebviewWindow, backdrop: String) -> Result<(), String> {
    if let Ok(store) = app.store("settings.json") {
        store.set("backdrop_type", serde_json::json!(backdrop));
        let _ = store.save();
    }
    unsafe {
        let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as HWND;
        let backdrop_type: i32 = match backdrop.as_str() {
            "mica" => 2,
            "acrylic" => 3,
            "tabbed" => 4,
            _ => 1,
        };
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            &raw const backdrop_type as *const _,
            std::mem::size_of::<i32>() as u32,
        );
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
#[tauri::command]
fn apply_backdrop(_window: tauri::WebviewWindow, _backdrop: String) -> Result<(), String> {
    Ok(())
}

#[tauri::command]
fn hide_window(window: tauri::WebviewWindow) -> Result<(), String> {
    window.hide().map_err(|e| e.to_string())
}

#[tauri::command]
fn show_window(window: tauri::WebviewWindow) -> Result<(), String> {
    window.show().map_err(|e| e.to_string())?;
    let _ = window.set_focus();
    Ok(())
}

#[tauri::command]
fn toggle_window_visibility(window: tauri::WebviewWindow) -> Result<(), String> {
    if window.is_visible().unwrap_or(false) {
        window.hide().map_err(|e| e.to_string())?;
    } else {
        window.show().map_err(|e| e.to_string())?;
        window.set_focus().ok();
    }
    Ok(())
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

/// Open a Tauri sub-window for a Origin panel (chat / systems / camera / ...).
/// If the window already exists, just focus and show it.
/// The label is also used as the `?window=<label>` query param so the React
/// router can mount the right component.
#[tauri::command]
fn open_subwindow(
    app: AppHandle,
    label: String,
    title: Option<String>,
    width: Option<u32>,
    height: Option<u32>,
) -> Result<(), String> {
    // Whitelist of valid sub-window labels
    let allowed = ["chat", "systems", "camera", "music", "news", "market", "settings"];
    if !allowed.contains(&label.as_str()) {
        return Err(format!("Sub-window '{}' not allowed", label));
    }

    if let Some(existing) = app.get_webview_window(&label) {
        existing.show().map_err(|e| e.to_string())?;
        existing.unminimize().ok();
        existing.set_focus().map_err(|e| e.to_string())?;
        return Ok(());
    }

    let url = format!("index.html?window={}", label);
    let (w, h) = match label.as_str() {
        "chat"    => (width.unwrap_or(720), height.unwrap_or(620)),
        "systems" => (width.unwrap_or(520), height.unwrap_or(680)),
        "camera"  => (width.unwrap_or(640), height.unwrap_or(540)),
        "music"   => (width.unwrap_or(420), height.unwrap_or(560)),
        "news"    => (width.unwrap_or(460), height.unwrap_or(620)),
        "market"  => (width.unwrap_or(420), height.unwrap_or(520)),
        "settings" => (width.unwrap_or(680), height.unwrap_or(720)),
        _         => (width.unwrap_or(640), height.unwrap_or(540)),
    };

    let display_title = title.unwrap_or_else(|| format!("Origin · {}", label.to_uppercase()));

    let new_window = WebviewWindowBuilder::new(&app, &label, WebviewUrl::App(url.into()))
        .title(&display_title)
        .inner_size(w as f64, h as f64)
        .min_inner_size(360.0, 320.0)
        .decorations(false)
        .transparent(true)
        .resizable(true)
        .skip_taskbar(false)
        .center()
        .visible(true)
        .shadow(true)
        .build()
        .map_err(|e| e.to_string())?;

    // Apply Mica backdrop to sub-windows too
    #[cfg(target_os = "windows")]
    {
        if let Ok(hwnd_wrapper) = new_window.hwnd() {
            let hwnd = hwnd_wrapper.0 as HWND;
            apply_mica_backdrop(hwnd);
        }
    }
    let _ = new_window; // suppress unused warning on non-windows

    Ok(())
}

#[tauri::command]
fn close_subwindow(app: AppHandle, label: String) -> Result<(), String> {
    if let Some(w) = app.get_webview_window(&label) {
        w.close().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn native_hello() -> String {
    origin_native::hello_native()
}

#[tauri::command]
fn send_notification(title: String, body: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        send_win_toast(&title, &body);
    }
    let _ = (&title, &body);
    Ok(())
}

#[cfg(target_os = "windows")]
fn sanitize_toast_text(s: &str) -> String {
    s.chars()
        .filter(|c| !matches!(c, '\'' | '`' | '"' | '$' | '{' | '}' | '(' | ')' | ';' | '|' | '&'))
        .take(256)
        .collect()
}

#[cfg(target_os = "windows")]
fn send_win_toast(title: &str, body: &str) {
    let title = sanitize_toast_text(title);
    let body = sanitize_toast_text(body);
    let ps_script = format!(
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; \
         $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); \
         $text = $template.GetElementsByTagName('text'); \
         $text.Item(0).AppendChild($template.CreateTextNode('{}')) > $null; \
         $text.Item(1).AppendChild($template.CreateTextNode('{}')) > $null; \
         $toast = [Windows.UI.Notifications.ToastNotification]::new($template); \
         [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Origin').Show($toast)",
        title, body
    );
    std::thread::spawn(move || {
        use std::os::windows::process::CommandExt;
        let _ = std::process::Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", &ps_script])
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .output();
    });
}

#[tauri::command]
fn register_context_menu() -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        register_explorer_context_menu().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn unregister_context_menu() -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        unregister_explorer_context_menu().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn get_context_menu_status() -> bool {
    #[cfg(target_os = "windows")]
    {
        return is_context_menu_registered();
    }
    #[cfg(not(target_os = "windows"))]
    false
}

#[tauri::command]
fn get_protocol_status() -> bool {
    #[cfg(target_os = "windows")]
    {
        return is_protocol_registered();
    }
    #[cfg(not(target_os = "windows"))]
    false
}

#[tauri::command]
fn install_powershell_profile(app: AppHandle) -> Result<String, String> {
    let root = origin_root(Some(&app));
    let script = root.join("scripts").join("origin.ps1");
    if !script.exists() {
        return Err(format!("Script not found: {}", script.display()));
    }
    let cmd = format!(
        ". '{}'; Install-OriginProfile",
        script.to_string_lossy()
    );
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let output = std::process::Command::new("powershell.exe")
            .args(["-ExecutionPolicy", "Bypass", "-NoProfile", "-Command", &cmd])
            .creation_flags(0x08000000)
            .output()
            .map_err(|e| e.to_string())?;
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        if !output.status.success() {
            return Err(format!("PowerShell failed: {}{}", stdout, stderr));
        }
        Ok(stdout)
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = cmd;
        Ok("PowerShell profile install is Windows-only".into())
    }
}


#[tauri::command]
fn get_autostart_status(app: AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    let mgr = app.autolaunch();
    if enabled {
        mgr.enable().map_err(|e| e.to_string())?;
    } else {
        mgr.disable().map_err(|e| e.to_string())?;
    }
    if let Ok(store) = app.store("settings.json") {
        store.set("autostart_enabled", serde_json::json!(enabled));
        let _ = store.save();
    }
    Ok(())
}

// ─── Win32 helpers (Windows-only) ──────────────────────────────

#[cfg(target_os = "windows")]
#[tauri::command]
fn make_tool_window(window: tauri::WebviewWindow) -> Result<(), String> {
    unsafe {
        let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as HWND;
        let ex_style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        // Add WS_EX_TOOLWINDOW to hide from Alt+Tab and ensure WS_EX_LAYERED for transparency
        let new_style = ex_style | WS_EX_TOOLWINDOW as isize | WS_EX_LAYERED as isize;
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new_style);
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
#[tauri::command]
fn make_tool_window(_window: tauri::WebviewWindow) -> Result<(), String> {
    Ok(())
}

// ─── Windows Explorer context menu ─────────────────────────────

#[cfg(target_os = "windows")]
fn origin_exe_path() -> String {
    std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| "origin.exe".to_string())
}

#[cfg(target_os = "windows")]
fn register_explorer_context_menu() -> Result<(), Box<dyn std::error::Error>> {
    use winreg::enums::*;
    use winreg::RegKey;

    let exe_path = origin_exe_path();
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);

    // Files: *\shell\Origin
    let (key, _) = hkcu.create_subkey("Software\\Classes\\*\\shell\\Origin")?;
    key.set_value("", &"Analyze with Origin")?;
    key.set_value("Icon", &format!("{},0", &exe_path))?;
    let (cmd_key, _) = hkcu.create_subkey("Software\\Classes\\*\\shell\\Origin\\command")?;
    cmd_key.set_value("", &format!("\"{}\" --analyze \"%1\"", &exe_path))?;

    // Folders: Directory\shell\Origin
    let (key, _) = hkcu.create_subkey("Software\\Classes\\Directory\\shell\\Origin")?;
    key.set_value("", &"Open in Origin")?;
    key.set_value("Icon", &format!("{},0", &exe_path))?;
    let (cmd_key, _) = hkcu.create_subkey("Software\\Classes\\Directory\\shell\\Origin\\command")?;
    cmd_key.set_value("", &format!("\"{}\" --open-folder \"%1\"", &exe_path))?;

    // Directory background: Directory\Background\shell\Origin
    let (key, _) = hkcu.create_subkey("Software\\Classes\\Directory\\Background\\shell\\Origin")?;
    key.set_value("", &"Open Origin here")?;
    key.set_value("Icon", &format!("{},0", &exe_path))?;
    let (cmd_key, _) = hkcu.create_subkey(
        "Software\\Classes\\Directory\\Background\\shell\\Origin\\command",
    )?;
    cmd_key.set_value("", &format!("\"{}\" --open-folder \"%V\"", &exe_path))?;

    log::info!("[origin] Explorer context menu registered");
    Ok(())
}

#[cfg(target_os = "windows")]
fn unregister_explorer_context_menu() -> Result<(), Box<dyn std::error::Error>> {
    use winreg::enums::*;
    use winreg::RegKey;

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let _ = hkcu.delete_subkey_all("Software\\Classes\\*\\shell\\Origin");
    let _ = hkcu.delete_subkey_all("Software\\Classes\\Directory\\shell\\Origin");
    let _ = hkcu.delete_subkey_all("Software\\Classes\\Directory\\Background\\shell\\Origin");

    log::info!("[origin] Explorer context menu unregistered");
    Ok(())
}

#[cfg(target_os = "windows")]
fn is_context_menu_registered() -> bool {
    use winreg::enums::*;
    use winreg::RegKey;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    hkcu.open_subkey("Software\\Classes\\*\\shell\\Origin").is_ok()
}

// ─── origin:// protocol handler (Windows registry) ────────────

#[cfg(target_os = "windows")]
fn register_origin_protocol() -> Result<(), Box<dyn std::error::Error>> {
    use winreg::enums::*;
    use winreg::RegKey;

    let exe_path = origin_exe_path();
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);

    let (key, _) = hkcu.create_subkey("Software\\Classes\\origin")?;
    key.set_value("", &"URL:Origin Protocol")?;
    key.set_value("URL Protocol", &"")?;

    let (icon_key, _) = hkcu.create_subkey("Software\\Classes\\origin\\DefaultIcon")?;
    icon_key.set_value("", &format!("{},0", &exe_path))?;

    let (cmd_key, _) = hkcu.create_subkey("Software\\Classes\\origin\\shell\\open\\command")?;
    cmd_key.set_value("", &format!("\"{}\" \"%1\"", &exe_path))?;

    log::info!("[origin] origin:// protocol handler registered");
    Ok(())
}

#[cfg(target_os = "windows")]
fn is_protocol_registered() -> bool {
    use winreg::enums::*;
    use winreg::RegKey;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    hkcu.open_subkey("Software\\Classes\\origin\\shell\\open\\command").is_ok()
}

/// Sets the taskbar progress state for the main window using Tauri's built-in API.
///  progress: 0.0 to 1.0 (or -1 for indeterminate, 0 to clear)
#[tauri::command]
fn set_taskbar_progress(app: AppHandle, progress: f64) -> Result<(), String> {
    use tauri::window::{ProgressBarState, ProgressBarStatus};

    let window = app
        .get_webview_window("main")
        .ok_or("main window not available")?;

    if progress < 0.0 {
        window
            .set_progress_bar(ProgressBarState {
                status: Some(ProgressBarStatus::Indeterminate),
                progress: None,
            })
            .map_err(|e| e.to_string())
    } else if progress == 0.0 {
        window
            .set_progress_bar(ProgressBarState {
                status: Some(ProgressBarStatus::None),
                progress: None,
            })
            .map_err(|e| e.to_string())
    } else {
        let pct = (progress.clamp(0.0, 1.0) * 100.0) as u64;
        window
            .set_progress_bar(ProgressBarState {
                status: Some(ProgressBarStatus::Normal),
                progress: Some(pct),
            })
            .map_err(|e| e.to_string())
    }
}

/// Enable Windows 11 Snap Layout support.
/// Makes the window respond to Win+Arrow keys and Aero Snap.
#[cfg(target_os = "windows")]
#[tauri::command]
fn enable_snap_layouts(window: tauri::WebviewWindow) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, GWL_STYLE,
        WS_SIZEBOX, WS_MAXIMIZEBOX, WS_MINIMIZEBOX,
    };
    unsafe {
        let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as HWND;
        let style = GetWindowLongPtrW(hwnd, GWL_STYLE);
        let new_style = style | (WS_SIZEBOX | WS_MAXIMIZEBOX | WS_MINIMIZEBOX) as isize;
        SetWindowLongPtrW(hwnd, GWL_STYLE, new_style);
    }
    log::info!("[origin] Snap layouts enabled");
    Ok(())
}

#[cfg(not(target_os = "windows"))]
#[tauri::command]
fn enable_snap_layouts(_window: tauri::WebviewWindow) -> Result<(), String> { Ok(()) }

/// Ensure window is visible/focused when switching virtual desktops.
#[tauri::command]
fn ensure_visible(window: tauri::WebviewWindow) -> Result<(), String> {
    if window.is_visible().unwrap_or(false) {
        let _ = window.set_focus();
    } else {
        window.show().map_err(|e| e.to_string())?;
        let _ = window.set_focus();
    }
    Ok(())
}

/// Minimize with animation, then hide to system tray.
#[cfg(target_os = "windows")]
#[tauri::command]
fn minimize_to_tray(window: tauri::WebviewWindow) -> Result<(), String> {
    unsafe {
        let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as HWND;
        windows_sys::Win32::UI::WindowsAndMessaging::ShowWindow(hwnd as _, 6); // SW_MINIMIZE
        let hwnd_send = hwnd as isize;
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(350));
            unsafe { windows_sys::Win32::UI::WindowsAndMessaging::ShowWindow(hwnd_send as _, 0); } // SW_HIDE
        });
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
#[tauri::command]
fn minimize_to_tray(_window: tauri::WebviewWindow) -> Result<(), String> { Ok(()) }

// ─── Python backend management ─────────────────────────────────

fn spawn_python_backend(app: &AppHandle) {
    use tauri_plugin_shell::process::CommandEvent;
    use tauri_plugin_shell::ShellExt;

    let pythonw = python_executable(Some(app));
    let root = origin_root(Some(app));
    let site_packages = root.join("venv").join("Lib").join("site-packages");

    log::info!(
        "[origin] Spawning Python backend: {} (cwd={})",
        pythonw.display(),
        root.display()
    );

    let shell = app.shell();
    let result = shell
        .command(pythonw.to_string_lossy().to_string())
        .args([
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "9001",
        ])
        .env("ORIGIN_HOST", "tauri")
        .env("PYTHONPATH", site_packages.to_string_lossy().to_string())
        .current_dir(root)
        .spawn();

    let (mut rx, _child) = match result {
        Ok(v) => v,
        Err(e) => {
            log::error!("[origin] Failed to spawn Python backend: {}", e);
            let _ = app.emit("backend-error", format!("Spawn failed: {}", e));
            return;
        }
    };

    let app_clone = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    if let Ok(s) = String::from_utf8(line) {
                        log::info!("[python] {}", s.trim_end());
                    }
                }
                CommandEvent::Stderr(line) => {
                    if let Ok(s) = String::from_utf8(line) {
                        log::warn!("[python] {}", s.trim_end());
                    }
                }
                CommandEvent::Terminated(payload) => {
                    log::warn!("[origin] Python backend terminated: {:?}", payload);
                    let _ = app_clone.emit("backend-terminated", payload.code);
                    if let Some(state) = app_clone.try_state::<AppState>() {
                        *state.backend_ready.lock().unwrap() = false;
                    }
                    log::info!("[origin] Scheduling backend restart in 2s...");
                    let restart_handle = app_clone.clone();
                    tauri::async_runtime::spawn(async move {
                        tokio::time::sleep(Duration::from_secs(2)).await;
                        spawn_python_backend(&restart_handle);
                        wait_for_backend(restart_handle).await;
                    });
                    return;
                }
                CommandEvent::Error(err) => {
                    log::error!("[origin] Python backend error: {}", err);
                    let _ = app_clone.emit("backend-error", err);
                }
                _ => {}
            }
        }
    });
}

async fn wait_for_backend(app: AppHandle) {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_millis(800))
        .build()
        .unwrap();
    let url = "http://127.0.0.1:9001/health";

    for attempt in 1..=150 {
        match client.get(url).send().await {
            Ok(resp) if resp.status().is_success() => {
                log::info!("[origin] Backend ready after {} attempts", attempt);
                if let Some(state) = app.try_state::<AppState>() {
                    *state.backend_ready.lock().unwrap() = true;
                }
                let _ = app.emit("backend-ready", ());

                // Read saved preferences from store
                let saved_mode = app
                    .store("settings.json")
                    .ok()
                    .and_then(|s| s.get("window_mode"))
                    .and_then(|v| v.as_str().map(String::from))
                    .unwrap_or_else(|| "integrated".into());

                if let Some(window) = app.get_webview_window("main") {
                    match saved_mode.as_str() {
                        "orb" => {
                            let _ = window.eval("if (location.search !== '?window=launcher') location.replace('index.html?window=launcher');");
                            let _ = window.set_always_on_top(true);
                            let _ = window.set_size(Size::Physical(PhysicalSize {
                                width: ORB_W, height: ORB_H,
                            }));
                            position_at_corner(&window);
                        }
                        "hud" | "full" => {
                            let _ = window.eval("if (location.search !== '?window=hud') location.replace('index.html?window=hud');");
                            let _ = window.set_always_on_top(false);
                            if let Ok(Some(monitor)) = window.primary_monitor() {
                                let screen = monitor.size();
                                let position = monitor.position();
                                let _ = window.set_size(Size::Physical(PhysicalSize {
                                    width: screen.width,
                                    height: screen.height,
                                }));
                                let _ = window.set_position(Position::Physical(PhysicalPosition {
                                    x: position.x,
                                    y: position.y,
                                }));
                            } else {
                                let _ = window.set_size(Size::Physical(PhysicalSize { width: 1280, height: 800 }));
                                let _ = window.center();
                            }
                        }
                        _ => {
                            let _ = window.eval("if (location.search !== '?window=integrated') location.replace('index.html?window=integrated');");
                            let _ = window.set_always_on_top(false);
                            let _ = window.set_size(Size::Physical(PhysicalSize {
                                width: INTEGRATED_W, height: INTEGRATED_H,
                            }));
                            let _ = window.center();
                        }
                    }
                    window.show().ok();
                    let _ = window.set_focus();

                    // Restore saved backdrop
                    #[cfg(target_os = "windows")]
                    {
                        let saved_backdrop = app
                            .store("settings.json")
                            .ok()
                            .and_then(|s| s.get("backdrop_type"))
                            .and_then(|v| v.as_str().map(String::from))
                            .unwrap_or_else(|| "mica".into());
                        if let Ok(hwnd_wrap) = window.hwnd() {
                            let hwnd = hwnd_wrap.0 as HWND;
                            let backdrop_val: i32 = match saved_backdrop.as_str() {
                                "mica" => 2,
                                "acrylic" => 3,
                                "tabbed" => 4,
                                _ => 1,
                            };
                            unsafe {
                                DwmSetWindowAttribute(
                                    hwnd,
                                    DWMWA_SYSTEMBACKDROP_TYPE,
                                    &raw const backdrop_val as *const _,
                                    std::mem::size_of::<i32>() as u32,
                                );
                            }
                        }
                    }
                }

                if let Some(state) = app.try_state::<AppState>() {
                    *state.window_mode.lock().unwrap() = saved_mode;
                }

                #[cfg(target_os = "windows")]
                send_win_toast("Origin", "Sistema en linea. Todos los subsistemas operativos.");
                return;
            }
            _ => {
                tokio::time::sleep(Duration::from_millis(200)).await;
            }
        }
    }
    log::error!("[origin] Backend failed to respond within 30s");
    let _ = app.emit("backend-error", "Timeout waiting for Python backend");
}

// ─── Tray ─────────────────────────────────────────────────────

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show_i = MenuItem::with_id(app, "show", "Show Origin", true, None::<&str>)?;
    let hide_i = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
    let sep1 = MenuItem::with_id(app, "sep1", "─────────", false, None::<&str>)?;
    let mode_int_i = MenuItem::with_id(app, "mode_integrated", "Mode: Integrated", true, None::<&str>)?;
    let mode_orb_i = MenuItem::with_id(app, "mode_orb", "Mode: Orb", true, None::<&str>)?;
    let mode_full_i = MenuItem::with_id(app, "mode_full", "Mode: HUD", true, None::<&str>)?;
    let sep2 = MenuItem::with_id(app, "sep2", "─────────", false, None::<&str>)?;
    let about_i = MenuItem::with_id(app, "about", "About Origin", true, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &show_i, &hide_i, &sep1, &mode_int_i, &mode_orb_i, &mode_full_i, &sep2, &about_i, &quit_i,
        ],
    )?;

    // Generate a simple icon if the file doesn't exist (fallback)
    let icon_path = app
        .path()
        .resource_dir()
        .ok()
        .map(|p| p.join("icons").join("icon.png"));

    let mut tray_builder = TrayIconBuilder::with_id("origin-tray")
        .menu(&menu)
        .tooltip("Origin — Portable System")
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    w.show().ok();
                    w.set_focus().ok();
                    let _ = app.emit("tray-action", "show");
                }
            }
            "hide" => {
                if let Some(w) = app.get_webview_window("main") {
                    w.hide().ok();
                }
            }
            "mode_integrated" => {
                let _ = app.emit("tray-action", "mode_integrated");
            }
            "mode_orb" => {
                let _ = app.emit("tray-action", "mode_orb");
            }
            "mode_full" => {
                let _ = app.emit("tray-action", "mode_full");
            }
            "about" => {
                let _ = app.emit("tray-action", "about");
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    if w.is_visible().unwrap_or(false) {
                        w.hide().ok();
                    } else {
                        w.show().ok();
                        w.set_focus().ok();
                    }
                }
            }
        });

    // Try to load icon from resources; if missing, use a minimal embedded 1x1 transparent png
    if let Some(p) = &icon_path {
        if p.exists() {
            if let Ok(img) = Image::from_path(p) {
                tray_builder = tray_builder.icon(img);
            }
        }
    }

    let _tray = tray_builder.build(app)?;
    Ok(())
}

// ─── Global Hotkeys ────────────────────────────────────────────

fn register_hotkeys(app: &AppHandle) -> Result<(), String> {
    let app_handle = app.clone();

    // Ctrl+Alt+Space → push-to-talk
    let ptt = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Space);
    // Ctrl+Alt+J → toggle window mode (orb ↔ full)
    let toggle = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyJ);
    // Ctrl+Alt+H → hide/show window
    let hide_show = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyH);

    app.global_shortcut()
        .on_shortcut(ptt, move |_app, _shortcut, event| {
            let state_str = match event.state {
                ShortcutState::Pressed => "Pressed",
                ShortcutState::Released => "Released",
            };
            let _ = app_handle.emit("hotkey-ptt", state_str);
        })
        .map_err(|e| format!("PTT hotkey: {}", e))?;

    let app_handle2 = app.clone();
    app.global_shortcut()
        .on_shortcut(toggle, move |_app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                let _ = app_handle2.emit("hotkey-toggle-mode", ());
            }
        })
        .map_err(|e| format!("Toggle hotkey: {}", e))?;

    let app_handle3 = app.clone();
    app.global_shortcut()
        .on_shortcut(hide_show, move |_app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                if let Some(w) = app_handle3.get_webview_window("main") {
                    if w.is_visible().unwrap_or(false) {
                        w.hide().ok();
                    } else {
                        w.show().ok();
                        w.set_focus().ok();
                    }
                }
            }
        })
        .map_err(|e| format!("Hide/show hotkey: {}", e))?;

    // Ctrl+Alt+V → analyze clipboard content
    let clip = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyV);
    let app_handle4 = app.clone();
    app.global_shortcut()
        .on_shortcut(clip, move |_app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                if let Some(w) = app_handle4.get_webview_window("main") {
                    w.show().ok();
                    w.set_focus().ok();
                }
                let _ = app_handle4.emit("hotkey-clipboard", ());
            }
        })
        .map_err(|e| format!("Clipboard hotkey: {}", e))?;

    // Ctrl+Alt+S → screenshot + analyze
    let screen = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyS);
    let app_handle5 = app.clone();
    app.global_shortcut()
        .on_shortcut(screen, move |_app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                let _ = app_handle5.emit("hotkey-screenshot", ());
            }
        })
        .map_err(|e| format!("Screenshot hotkey: {}", e))?;

    // Ctrl+Alt+Q → quick query overlay
    let quick = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyQ);
    let app_handle6 = app.clone();
    app.global_shortcut()
        .on_shortcut(quick, move |_app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                if let Some(w) = app_handle6.get_webview_window("main") {
                    w.show().ok();
                    w.set_focus().ok();
                }
                let _ = app_handle6.emit("hotkey-quick-query", ());
            }
        })
        .map_err(|e| format!("Quick query hotkey: {}", e))?;

    log::info!("[origin] Global hotkeys registered: Ctrl+Alt+Space (PTT), Ctrl+Alt+J (toggle), Ctrl+Alt+H (hide), Ctrl+Alt+V (clipboard), Ctrl+Alt+S (screenshot), Ctrl+Alt+Q (quick query)");
    Ok(())
}

// ─── Main entry ────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // Second instance: forward deep-link or CLI args, then show window
            if argv.len() > 1 {
                let arg = &argv[1];
                if arg.starts_with("origin://") {
                    let _ = app.emit("deep-link", arg.clone());
                } else if arg == "--analyze" && argv.len() > 2 {
                    let _ = app.emit("analyze-file", argv[2].clone());
                } else if arg == "--open-folder" && argv.len() > 2 {
                    let _ = app.emit("open-folder", argv[2].clone());
                }
            }
            if let Some(w) = app.get_webview_window("main") {
                w.show().ok();
                w.set_focus().ok();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            backend_ready,
            set_click_through,
            set_window_mode,
            toggle_window_visibility,
            hide_window,
            show_window,
            make_tool_window,
            apply_backdrop,
            quit_app,
            native_hello,
            get_autostart_status,
            set_autostart,
            open_subwindow,
            close_subwindow,
            send_notification,
            register_context_menu,
            unregister_context_menu,
            get_context_menu_status,
            get_protocol_status,
            set_taskbar_progress,
            install_powershell_profile,
            enable_snap_layouts,
            ensure_visible,
            minimize_to_tray,
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();

            // 1. Build system tray
            if let Err(e) = build_tray(&app_handle) {
                log::error!("[origin] Tray build failed: {}", e);
            }

            // 2. Register global hotkeys
            if let Err(e) = register_hotkeys(&app_handle) {
                log::error!("[origin] Hotkey registration failed: {}", e);
            }

            // 3. Spawn Python backend
            spawn_python_backend(&app_handle);

            // 4. Wait for backend ready, then show window
            let app_for_wait = app_handle.clone();
            tauri::async_runtime::spawn(async move {
                wait_for_backend(app_for_wait).await;
            });

            // 5. Apply Mica backdrop on Windows 11 (native integration)
            #[cfg(target_os = "windows")]
            {
                if let Some(window) = app.get_webview_window("main") {
                    if let Ok(hwnd_wrapper) = window.hwnd() {
                        let hwnd = hwnd_wrapper.0 as HWND;
                        apply_mica_backdrop(hwnd);
                        log::info!("[origin] Mica backdrop applied to main window");
                    }
                }
            }

            // 6. Register origin:// protocol handler if not yet registered
            #[cfg(target_os = "windows")]
            {
                if !is_protocol_registered() {
                    if let Err(e) = register_origin_protocol() {
                        log::warn!("[origin] Protocol registration failed: {}", e);
                    }
                }
            }

            // 7. Handle deep-link and CLI arguments (origin://, --analyze, --open-folder)
            {
                let args: Vec<String> = std::env::args().collect();
                let deep_handle = app_handle.clone();
                if args.len() > 1 {
                    let arg = &args[1];
                    if arg.starts_with("origin://") {
                        log::info!("[origin] Deep link opened: {}", arg);
                        let _ = deep_handle.emit("deep-link", arg.clone());
                    } else if arg == "--analyze" && args.len() > 2 {
                        log::info!("[origin] Analyze file: {}", &args[2]);
                        let _ = deep_handle.emit("analyze-file", args[2].clone());
                    } else if arg == "--open-folder" && args.len() > 2 {
                        log::info!("[origin] Open folder: {}", &args[2]);
                        let _ = deep_handle.emit("open-folder", args[2].clone());
                    }
                }
            }

            // 8. Enable autostart on first run
            let already_initialized = app_handle
                .store("settings.json")
                .ok()
                .and_then(|s| s.get("autostart_initialized"))
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            if !already_initialized {
                let mgr = app_handle.autolaunch();
                match mgr.enable() {
                    Ok(_) => log::info!("[origin] Autostart enabled on first run"),
                    Err(e) => log::warn!("[origin] Autostart enable failed: {}", e),
                }
                if let Ok(store) = app_handle.store("settings.json") {
                    store.set("autostart_initialized", serde_json::json!(true));
                    store.set("autostart_enabled", serde_json::json!(true));
                    let _ = store.save();
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                // Don't actually close — hide to tray instead
                api.prevent_close();
                window.hide().ok();
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            if let RunEvent::ExitRequested { api: _, .. } = event {
                // App is exiting cleanly
            }
        });
}
