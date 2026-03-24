#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod tray;

use mouse_position::mouse_position::Mouse;
use tauri::Manager;

#[tauri::command]
fn get_mouse_position() -> serde_json::Value {
    match Mouse::get_mouse_position() {
        Mouse::Position { x, y } => serde_json::json!({
            "clientX": x,
            "clientY": y,
        }),
        Mouse::Error => serde_json::json!(null),
    }
}

fn main() {
    std::env::set_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--ignore-gpu-blocklist");

    tauri::Builder::default()
        .setup(|app| {
            let window = app.get_window("main").expect("main window should exist");
            window.set_ignore_cursor_events(true)?;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_mouse_position])
        .system_tray(tray::build_system_tray())
        .on_system_tray_event(tray::handle_tray_event)
        .run(tauri::generate_context!())
        .expect("error while running emoticore shell");
}
