import { useEffect, useRef } from "react";
import { buildWsUrl } from "../api/client";
import { TERMINAL_STATUSES } from "../lib/constants";

export function useJobSocket({ token, currentJobId, currentJobStatus, appendEvent, onTerminalEvent, pollLatest, setWsStatus }) {
  const callbacks = useRef({ appendEvent, onTerminalEvent, pollLatest, setWsStatus });
  callbacks.current = { appendEvent, onTerminalEvent, pollLatest, setWsStatus };

  useEffect(() => {
    if (!token || !currentJobId || TERMINAL_STATUSES.has(currentJobStatus)) {
      callbacks.current.setWsStatus("getrennt");
      return undefined;
    }

    let socket = null;
    let reconnectTimer = null;
    let pollTimer = null;
    let stopped = false;

    const stopPolling = () => {
      if (pollTimer) window.clearInterval(pollTimer);
      pollTimer = null;
    };

    const startPolling = () => {
      if (pollTimer) return;
      pollTimer = window.setInterval(() => callbacks.current.pollLatest?.(), 5000);
    };

    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(buildWsUrl(`/ws/jobs/${currentJobId}`, token));
      callbacks.current.setWsStatus("verbinde");

      socket.onopen = () => {
        stopPolling();
        callbacks.current.setWsStatus("verbunden");
        callbacks.current.appendEvent?.({ event_type: "frontend", level: "info", message: "WebSocket verbunden" });
      };

      socket.onmessage = (message) => {
        try {
          const data = JSON.parse(message.data);
          callbacks.current.appendEvent?.(data);
          if (["job.completed", "job.failed", "job.cancelled"].includes(data.event_type)) {
            callbacks.current.onTerminalEvent?.(data);
          }
        } catch (error) {
          callbacks.current.appendEvent?.({
            event_type: "frontend",
            level: "warning",
            message: `Ungültige WebSocket-Nachricht: ${error.message}`,
          });
        }
      };

      socket.onerror = () => callbacks.current.setWsStatus("fehler");

      socket.onclose = () => {
        callbacks.current.setWsStatus("getrennt");
        if (stopped) return;
        startPolling();
        reconnectTimer = window.setTimeout(connect, 3000);
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      stopPolling();
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      callbacks.current.setWsStatus("getrennt");
    };
  }, [token, currentJobId, currentJobStatus]);
}
