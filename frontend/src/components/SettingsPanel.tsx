import { useCallback, useEffect, useState } from "react";
import { isTauri } from "../config/api";

export function SettingsPanel() {
  const [autostart, setAutostart] = useState(false);
  const [backdrop, setBackdrop] = useState("mica");
  const [contextMenu, setContextMenu] = useState(false);
  const [protocolActive, setProtocolActive] = useState(false);

  useEffect(() => {
    if (!isTauri()) return;
    void import("@tauri-apps/api/core").then(({ invoke }) => {
      invoke<boolean>("get_autostart_status").then(setAutostart).catch(() => {});
      invoke<boolean>("get_context_menu_status").then(setContextMenu).catch(() => {});
      invoke<boolean>("get_protocol_status").then(setProtocolActive).catch(() => {});
    });
  }, []);

  const toggleAutostart = useCallback(async () => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    const next = !autostart;
    try {
      await invoke("set_autostart", { enabled: next });
      setAutostart(next);
    } catch (e) {
      console.error("set_autostart failed:", e);
    }
  }, [autostart]);

  const toggleContextMenu = useCallback(async () => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      if (contextMenu) {
        await invoke("unregister_context_menu");
        setContextMenu(false);
      } else {
        await invoke("register_context_menu");
        setContextMenu(true);
      }
    } catch (e) {
      console.error("context_menu toggle failed:", e);
    }
  }, [contextMenu]);

  const testNotification = useCallback(async () => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      await invoke("send_notification", {
        title: "Origin",
        body: "Las notificaciones de Windows estan funcionando correctamente.",
      });
    } catch (e) {
      console.error("notification failed:", e);
    }
  }, []);

  const changeBackdrop = useCallback(async (type: string) => {
    setBackdrop(type);
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      await invoke("apply_backdrop", { backdrop: type });
    } catch (e) {
      console.error("apply_backdrop failed:", e);
    }
  }, []);

  const installPowerShell = useCallback(async () => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      const result = await invoke<string>("install_powershell_profile");
      console.log("PowerShell profile installed:", result);
      await invoke("send_notification", {
        title: "Origin",
        body: "PowerShell profile instalado. Reinicia tu terminal.",
      });
    } catch (e) {
      console.error("installPowerShell failed:", e);
    }
  }, []);

  const setMode = useCallback(async (mode: "integrated" | "hud" | "orb") => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      await invoke("set_window_mode", { mode });
      if (mode === "orb" || mode === "hud" || mode === "integrated") {
        const url = new URL(window.location.href);
        url.searchParams.set("window", mode === "orb" ? "launcher" : mode);
        window.location.assign(url.toString());
      }
    } catch (e) {
      console.error("set_window_mode failed:", e);
    }
  }, []);

  return (
    <div className="settings-panel">
      <h2 className="settings-panel__title">Configuracion</h2>

      <section className="settings-panel__section">
        <h3 className="settings-panel__heading">Ventana</h3>

        <div className="settings-panel__row">
          <div className="settings-panel__label">
            <strong>Modo de ventana</strong>
            <span>Como se muestra Origin en el escritorio</span>
          </div>
          <div className="settings-panel__buttons">
            <button className="settings-panel__chip" onClick={() => setMode("integrated")}>
              Integrado
            </button>
            <button className="settings-panel__chip" onClick={() => setMode("hud")}>
              HUD
            </button>
            <button className="settings-panel__chip" onClick={() => setMode("orb")}>
              Orbe
            </button>
          </div>
        </div>

        <div className="settings-panel__row">
          <div className="settings-panel__label">
            <strong>Fondo (Backdrop)</strong>
            <span>Efecto visual de Windows 11</span>
          </div>
          <div className="settings-panel__buttons">
            {(["mica", "acrylic", "tabbed", "none"] as const).map((b) => (
              <button
                key={b}
                className={`settings-panel__chip ${backdrop === b ? "settings-panel__chip--active" : ""}`}
                onClick={() => changeBackdrop(b)}
              >
                {b === "mica" ? "Mica" : b === "acrylic" ? "Acrilico" : b === "tabbed" ? "Mica Alt" : "Ninguno"}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="settings-panel__section">
        <h3 className="settings-panel__heading">Sistema</h3>

        <div className="settings-panel__row">
          <div className="settings-panel__label">
            <strong>Inicio automatico</strong>
            <span>Origin se inicia con Windows</span>
          </div>
          <button
            className={`settings-panel__toggle ${autostart ? "settings-panel__toggle--on" : ""}`}
            onClick={toggleAutostart}
          >
            <span className="settings-panel__toggle-thumb" />
          </button>
        </div>
      </section>

      <section className="settings-panel__section">
        <h3 className="settings-panel__heading">Integracion con Windows</h3>

        <div className="settings-panel__row">
          <div className="settings-panel__label">
            <strong>Menu contextual del Explorador</strong>
            <span>"Analyze with Origin" en click derecho</span>
          </div>
          <button
            className={`settings-panel__toggle ${contextMenu ? "settings-panel__toggle--on" : ""}`}
            onClick={toggleContextMenu}
          >
            <span className="settings-panel__toggle-thumb" />
          </button>
        </div>

        <div className="settings-panel__row">
          <div className="settings-panel__label">
            <strong>Notificaciones de Windows</strong>
            <span>Alertas nativas del sistema</span>
          </div>
          <button className="settings-panel__chip" onClick={testNotification}>
            Probar
          </button>
        </div>

        <div className="settings-panel__row">
          <div className="settings-panel__label">
            <strong>Protocolo origin://</strong>
            <span>Deep links: origin://chat, origin://systems, etc.</span>
          </div>
          <span className={`settings-panel__status ${protocolActive ? "" : "settings-panel__status--inactive"}`}>
            {protocolActive ? "Activo" : "Inactivo"}
          </span>
        </div>
      </section>

      <section className="settings-panel__section">
        <h3 className="settings-panel__heading">Atajos de teclado</h3>
        <div className="settings-panel__shortcuts">
          <div className="settings-panel__shortcut">
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Space</kbd>
            <span>Push-to-talk</span>
          </div>
          <div className="settings-panel__shortcut">
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Q</kbd>
            <span>Consulta rapida</span>
          </div>
          <div className="settings-panel__shortcut">
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>V</kbd>
            <span>Analizar clipboard</span>
          </div>
          <div className="settings-panel__shortcut">
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>S</kbd>
            <span>Captura de pantalla</span>
          </div>
          <div className="settings-panel__shortcut">
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>J</kbd>
            <span>Cambiar modo</span>
          </div>
          <div className="settings-panel__shortcut">
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>H</kbd>
            <span>Ocultar / Mostrar</span>
          </div>
        </div>
      </section>

      <section className="settings-panel__section">
        <h3 className="settings-panel__heading">Terminal (PowerShell)</h3>
        <div className="settings-panel__row">
          <div className="settings-panel__label">
            <strong>Comando origin en terminal</strong>
            <span>Usa Origin desde cualquier PowerShell</span>
          </div>
          <button className="settings-panel__chip" onClick={installPowerShell}>
            Instalar
          </button>
        </div>
        <div className="settings-panel__code">
          <code>origin "pregunta"</code> · <code>origin-status</code> · <code>origin-clipboard</code> · <code>origin-open chat</code>
        </div>
      </section>

      <footer className="settings-panel__footer">
        <span className="settings-panel__version">Origin v0.1.0</span>
        <span className="settings-panel__build">Tauri 2.x + FastAPI + React</span>
      </footer>
    </div>
  );
}
