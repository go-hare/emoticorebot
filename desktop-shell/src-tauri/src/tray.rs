use tauri::{
    AppHandle, CustomMenuItem, Manager, SystemTray, SystemTrayEvent, SystemTrayMenu,
    SystemTrayMenuItem, WindowUrl,
};

pub fn build_system_tray() -> SystemTray {
    let menu = SystemTrayMenu::new()
        .add_item(CustomMenuItem::new("show_overlay".to_string(), "Show Pet"))
        .add_item(CustomMenuItem::new("hide_overlay".to_string(), "Hide Pet"))
        .add_item(CustomMenuItem::new("setting".to_string(), "Setting"))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("quit".to_string(), "Quit"));

    SystemTray::new().with_menu(menu)
}

pub fn handle_tray_event(app: &AppHandle, event: SystemTrayEvent) {
    match event {
        SystemTrayEvent::MenuItemClick { id, .. } => match id.as_str() {
            "show_overlay" => show_overlay(app),
            "hide_overlay" => hide_overlay(app),
            "setting" => show_setting_window(app),
            "quit" => app.exit(0),
            _ => {}
        },
        SystemTrayEvent::DoubleClick { .. } => show_setting_window(app),
        _ => {}
    }
}

fn show_overlay(app: &AppHandle) {
    if let Some(window) = app.get_window("main") {
        let _ = window.show();
        let _ = window.set_ignore_cursor_events(true);
    }
}

fn hide_overlay(app: &AppHandle) {
    if let Some(window) = app.get_window("main") {
        let _ = window.hide();
    }
}

fn show_setting_window(app: &AppHandle) {
    if let Some(window) = app.get_window("setting") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return;
    }

    let _ = tauri::WindowBuilder::new(app, "setting", WindowUrl::App("index.html?setting=1".into()))
        .title("EmotiCore Setting")
        .inner_size(1040.0, 720.0)
        .min_inner_size(900.0, 640.0)
        .resizable(true)
        .build();
}
