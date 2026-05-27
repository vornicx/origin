import "@testing-library/jest-dom";

// Stub Tauri globals so unit tests run outside Electron/WebView2
Object.defineProperty(window, "__TAURI__", { value: undefined, writable: true });
Object.defineProperty(window, "__TAURI_INTERNALS__", { value: undefined, writable: true });
