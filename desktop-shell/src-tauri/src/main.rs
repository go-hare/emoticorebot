#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod tray;

use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let window = app.get_window("main").expect("main window should exist");
            window.set_always_on_top(true)?;
            Ok(())
        })
        .system_tray(tray::build_system_tray())
        .on_system_tray_event(tray::handle_tray_event)
        .run(tauri::generate_context!())
        .expect("error while running emoticore shell");
}
