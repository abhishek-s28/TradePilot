"use client";
import { useEffect } from "react";
import { useMarketStore } from "@/lib/marketStore";

export function useMarketWS() {
  const { setQuotes, setAccount, setConnected } = useMarketStore();

  useEffect(() => {
    let ws: WebSocket | null = null;
    let cancelled = false;
    let delay = 1000;

    const connect = () => {
      if (cancelled) return;
      const base = process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000";
      ws = new WebSocket(`${base}/ws/market`);

      ws.onopen = () => {
        setConnected(true);
        delay = 1000;
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "market_update") {
            if (msg.quotes)  setQuotes(msg.quotes);
            if (msg.account) setAccount(msg.account);
          }
        } catch { /* ignore */ }
      };

      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) {
          setTimeout(connect, delay);
          delay = Math.min(delay * 2, 30_000);
        }
      };

      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [setQuotes, setAccount, setConnected]);
}
