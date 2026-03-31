import { useEffect, useRef, useState } from "react";

export type SseConnectionState = "idle" | "connecting" | "live" | "reconnecting" | "offline";

export interface SseEventPayload {
  type: string;
  data: unknown;
}

export interface UseSSEOptions {
  enabled?: boolean;
  eventTypes?: string[];
  onEvent?: (event: SseEventPayload) => void;
  onError?: () => void;
}

const DEFAULT_EVENT_TYPES = [
  "stage.changed",
  "approval.changed",
  "artifact.created",
  "artifact.updated",
  "job.updated",
  "job.completed",
  "job.failed",
  "run.completed",
  "run.failed",
  "run.cancelled",
];

function parseEventData(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export function useSSE(url: string | null | undefined, options: UseSSEOptions = {}) {
  const { enabled = true, onEvent, onError } = options;
  const eventTypes = options.eventTypes ?? DEFAULT_EVENT_TYPES;
  const [state, setState] = useState<SseConnectionState>(url && enabled ? "connecting" : "idle");
  const callbackRef = useRef(onEvent);
  const errorRef = useRef(onError);

  useEffect(() => {
    callbackRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    errorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    if (!url || !enabled) {
      setState("idle");
      return undefined;
    }

    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      setState("offline");
      return undefined;
    }

    const source = new EventSource(url);
    const listeners = eventTypes.map((eventType) => {
      const handler = (event: MessageEvent<string>) => {
        setState("live");
        callbackRef.current?.({
          type: eventType,
          data: parseEventData(event.data),
        });
      };
      source.addEventListener(eventType, handler as EventListener);
      return { eventType, handler };
    });

    source.onopen = () => {
      setState("live");
    };

    source.onmessage = (event) => {
      setState("live");
      callbackRef.current?.({ type: "message", data: parseEventData(event.data) });
    };

    source.onerror = () => {
      setState(source.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
      errorRef.current?.();
    };

    return () => {
      listeners.forEach(({ eventType, handler }) => {
        source.removeEventListener(eventType, handler as EventListener);
      });
      source.close();
      setState("idle");
    };
  }, [enabled, eventTypes, url]);

  return { state, isLive: state === "live" };
}
