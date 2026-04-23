import { useState } from "react";
import { actOnGate } from "../api/gates";
import { ApiError } from "../api/client";
import type { GateInfo } from "../types";
import { StatusBadge } from "./StatusBadge";

type Props = {
  gateId: string;
  gateInfo: GateInfo | null | undefined;
  onAction?: () => void | Promise<void>;
};

export function GateActionPanel({ gateId, gateInfo, onAction }: Props) {
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");

  if (!gateInfo) {
    return (
      <div className="rounded-2xl border border-border bg-panel-strong/80 p-4 text-sm text-muted">
        当前未进入闸门。
      </div>
    );
  }

  const waiting = gateInfo.status === "waiting";

  async function runAction(action: "approve" | "reject") {
    setPending(action);
    setError(null);
    try {
      await actOnGate(gateId, action, { note: note.trim() || undefined });
      await onAction?.();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("闸门操作失败");
      }
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="space-y-3 rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-copy">闸门 {gateInfo.gate_key ?? gateId}</p>
          {gateInfo.actor ? (
            <p className="mt-1 text-xs text-muted">操作人 {gateInfo.actor}</p>
          ) : null}
          {gateInfo.acted_at ? (
            <p className="mt-1 text-xs text-muted">{gateInfo.acted_at}</p>
          ) : null}
        </div>
        <StatusBadge status={gateInfo.status} />
      </div>

      {gateInfo.note ? <p className="text-xs text-muted">备注：{gateInfo.note}</p> : null}

      {waiting ? (
        <label className="block space-y-1 text-xs text-muted">
          <span>备注（可选）</span>
          <textarea
            className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
            onChange={(event) => setNote(event.target.value)}
            rows={2}
            value={note}
          />
        </label>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <button
          className="rounded-lg bg-success px-3 py-1.5 text-xs font-medium text-ink-invert disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!waiting || pending !== null}
          onClick={() => void runAction("approve")}
          type="button"
        >
          {pending === "approve" ? "批准中..." : "批准"}
        </button>
        <button
          className="rounded-lg bg-danger px-3 py-1.5 text-xs font-medium text-ink-invert disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!waiting || pending !== null}
          onClick={() => void runAction("reject")}
          type="button"
        >
          {pending === "reject" ? "驳回中..." : "驳回"}
        </button>
      </div>

      {error ? <p className="text-xs text-danger">{error}</p> : null}
    </div>
  );
}
