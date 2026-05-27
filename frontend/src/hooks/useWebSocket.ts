import { useState, useEffect, useRef, useCallback } from "react";
import { ConnectionStatus, WSMessage } from "../types";
import { ws, apiBase } from "../config/api";

const INITIAL_DELAY_MS = 2000;
const MAX_DELAY_MS = 15000;

export function useWebSocket(onMessage: (msg: WSMessage) => void) {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMounted = useRef(true);
  const retriesRef = useRef(0);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      await fetch(`${apiBase()}/health`, { signal: AbortSignal.timeout(800) });
    } catch {
      if (!isMounted.current) return;
      const delay = Math.min(INITIAL_DELAY_MS * Math.pow(1.5, retriesRef.current), MAX_DELAY_MS);
      retriesRef.current++;
      reconnectTimer.current = setTimeout(connect, delay);
      return;
    }

    if (!isMounted.current) return;
    setStatus("connecting");
    const socket = new WebSocket(ws("/ws/chat"));
    wsRef.current = socket;

    connectTimeoutRef.current = setTimeout(() => {
      if (socket.readyState !== WebSocket.OPEN) {
        socket.close();
      }
    }, 5000);

    socket.onopen = () => {
      if (connectTimeoutRef.current) clearTimeout(connectTimeoutRef.current);
      if (!isMounted.current) return;
      retriesRef.current = 0;
      setStatus("connected");
    };

    socket.onmessage = (event) => {
      if (!isMounted.current) return;
      try {
        const data: WSMessage = JSON.parse(event.data);
        onMessageRef.current(data);
      } catch {}
    };

    socket.onerror = () => {
      if (!isMounted.current) return;
      setStatus("error");
    };

    socket.onclose = () => {
      if (!isMounted.current) return;
      setStatus("disconnected");
      const delay = Math.min(INITIAL_DELAY_MS * Math.pow(1.5, retriesRef.current), MAX_DELAY_MS);
      retriesRef.current++;
      reconnectTimer.current = setTimeout(connect, delay);
    };
  }, []);

  useEffect(() => {
    isMounted.current = true;
    connect();
    return () => {
      isMounted.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (connectTimeoutRef.current) clearTimeout(connectTimeoutRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((content: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
    wsRef.current.send(JSON.stringify({ type: "message", content }));
    return true;
  }, []);

  return { status, send };
}
