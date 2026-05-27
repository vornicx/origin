import { describe, it, expect, beforeEach } from "vitest";
import { isTauri, backendHost, apiBase, wsBase, api, ws } from "./api";

describe("isTauri", () => {
  beforeEach(() => {
    delete (window as any).__TAURI__;
    delete (window as any).__TAURI_INTERNALS__;
  });

  it("returns false when no Tauri globals present", () => {
    expect(isTauri()).toBe(false);
  });

  it("returns true when __TAURI__ is present", () => {
    (window as any).__TAURI__ = {};
    expect(isTauri()).toBe(true);
  });

  it("returns true when __TAURI_INTERNALS__ is present", () => {
    (window as any).__TAURI_INTERNALS__ = {};
    expect(isTauri()).toBe(true);
  });
});

describe("backendHost", () => {
  it("returns 127.0.0.1:9001 in Tauri context", () => {
    (window as any).__TAURI__ = {};
    expect(backendHost()).toBe("127.0.0.1:9001");
    delete (window as any).__TAURI__;
  });

  it("uses window.location.hostname in browser context", () => {
    expect(backendHost()).toContain(":9001");
  });
});

describe("apiBase", () => {
  it("starts with http:// in non-Tauri browser context", () => {
    expect(apiBase()).toMatch(/^https?:\/\//);
  });

  it("ends without trailing slash", () => {
    expect(apiBase()).not.toMatch(/\/$/);
  });
});

describe("wsBase", () => {
  it("starts with ws:// or wss://", () => {
    expect(wsBase()).toMatch(/^wss?:\/\//);
  });
});

describe("api helper", () => {
  it("prepends leading slash if missing", () => {
    const result = api("health");
    expect(result).toContain("/health");
  });

  it("does not double-slash when path starts with /", () => {
    const result = api("/health");
    expect(result).not.toContain("//health");
  });
});

describe("ws helper", () => {
  it("returns a ws:// URL", () => {
    expect(ws("/chat")).toMatch(/^wss?:\/\//);
    expect(ws("/chat")).toContain("/chat");
  });
});
