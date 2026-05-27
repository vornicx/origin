/**
 * Centralized API configuration.
 *
 * Detects runtime context:
 *   - Browser: uses window.location.host (allows access from LAN)
 *   - Tauri:   uses 127.0.0.1:9001 (tauri.localhost won't resolve to Python)
 *
 * This is the single source of truth for backend URLs.
 */

declare global {
  interface Window {
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
  }
}

/** Returns true if we're running inside the Tauri WebView. */
export function isTauri(): boolean {
  if (typeof window === "undefined") return false;
  return "__TAURI__" in window || "__TAURI_INTERNALS__" in window;
}

/** Host:port for backend (no protocol). */
export function backendHost(): string {
  if (isTauri()) return "127.0.0.1:9001";
  if (typeof window === "undefined") return "localhost:9001";
  // Browser mode: prefer current host so LAN clients work
  const host = window.location.hostname || "localhost";
  return `${host}:9001`;
}

/** HTTP base URL for REST API. */
export function apiBase(): string {
  if (typeof window === "undefined") return "http://localhost:9001";
  const proto = isTauri()
    ? "http:"
    : window.location.protocol === "https:"
    ? "https:"
    : "http:";
  return `${proto}//${backendHost()}`;
}

/** WebSocket base URL. */
export function wsBase(): string {
  if (typeof window === "undefined") return "ws://localhost:9001";
  const proto = isTauri()
    ? "ws:"
    : window.location.protocol === "https:"
    ? "wss:"
    : "ws:";
  return `${proto}//${backendHost()}`;
}

/** Convenience: full URL for a given path. */
export function api(path: string): string {
  return `${apiBase()}${path.startsWith("/") ? path : "/" + path}`;
}

/** Convenience: full WebSocket URL for a given path. */
export function ws(path: string): string {
  return `${wsBase()}${path.startsWith("/") ? path : "/" + path}`;
}
