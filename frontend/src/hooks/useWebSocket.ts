"use client";
import { useEffect, useRef, useState } from "react";

export function useWebSocket<T = unknown>(path: string) {
  const [message, setMessage] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let reconnectDelay = 1000;

    const connect = () => {
      if (cancelled) return;
      // In dev with rewrites we'd need direct backend URL for WS
      const base = process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000";
      const url = `${base}${path}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => {
        setConnected(true);
        reconnectDelay = 1000;
      };
      ws.onmessage = (ev) => {
        try {
          setMessage(JSON.parse(ev.data) as T);
        } catch {
          /* ignore non-JSON */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) {
          setTimeout(connect, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, 30_000);
        }
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [path]);

  return { message, connected };
}
