import { useEffect } from "react";
import OverlayApp from "./OverlayApp";
import ShellApp from "./ShellApp";

function isSettingWindow(): boolean {
  return new URLSearchParams(window.location.search).get("setting") === "1";
}

export default function App() {
  const settingWindow = isSettingWindow();

  useEffect(() => {
    document.body.classList.toggle("window-overlay", !settingWindow);
    document.body.classList.toggle("window-shell", settingWindow);
    return () => {
      document.body.classList.remove("window-overlay", "window-shell");
    };
  }, [settingWindow]);

  return settingWindow ? <ShellApp /> : <OverlayApp />;
}
