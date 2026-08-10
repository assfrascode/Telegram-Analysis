import { useEffect, useRef } from "react";
import { apiJson, buildWsUrl } from "../api/client";
import { TERMINAL_STATUSES } from "../lib/constants";

export function useJobSocket({ token, currentJobId, currentJobStatus, appendEvent, onTerminalEvent, pollLatest, setWsStatus }) {
  const callbacks = useRef({ appendEvent, onTerminalEvent, pollLatest, setWsStatus });
  callbacks.current = { appendEvent, onTerminalEvent, pollLatest, setWsStatus };

  useEffect(() => {
    if (!token || !currentJobId || TERMINAL_STATUSES.has(currentJobStatus)) {
      callbacks.current.setWsStatus("disconnected");
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

    const connect = async () => {
      if (stopped) return;
      callbacks.current.setWsStatus("connecting");

      try {
        const { ticket } = await apiJson(`/jobs/${currentJobId}/ws-ticket`, {
          token,
          method: "POST",
        });
        if (stopped) return;
        socket = new WebSocket(buildWsUrl(`/ws/jobs/${currentJobId}`, ticket));
      } catch (error) {
        callbacks.current.setWsStatus("error");
        callbacks.current.appendEvent?.({
          event_type: "frontend",
          level: "warning",
          message: `Live-update ticket failed: ${error.message}`,
        });
        startPolling();
        if (!stopped) reconnectTimer = window.setTimeout(connect, 3000);
        return;
      }

      socket.onopen = () => {
        stopPolling();
        callbacks.current.setWsStatus("connected");
        callbacks.current.appendEvent?.({ event_type: "frontend", level: "info", message: "Live updates connected" });
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
            message: `Invalid live update message: ${error.message}`,
          });
        }
      };

      socket.onerror = () => callbacks.current.setWsStatus("error");

      socket.onclose = () => {
        callbacks.current.setWsStatus("disconnected");
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
      callbacks.current.setWsStatus("disconnected");
    };
  }, [token, currentJobId, currentJobStatus]);
}
