import { useState } from "react";
import type { GateName } from "../types";
import { approveRun, rejectRun } from "../api/runs";

const DEFAULT_REJECT_REASON = "Rejected from dashboard";

export function ApprovalAction({
  runId,
  gate,
  by,
  comment,
  reason,
  onComplete,
}: {
  runId: string;
  gate: GateName;
  by: string;
  comment?: string;
  reason?: string;
  onComplete?: () => void | Promise<void>;
}) {
  const [pendingAction, setPendingAction] = useState<"approve" | "reject" | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleApprove() {
    setPendingAction("approve");
    setErrorMessage(null);
    try {
      await approveRun(runId, { gate, by, comment });
      await onComplete?.();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Approval failed");
    } finally {
      setPendingAction(null);
    }
  }

  async function handleReject() {
    setPendingAction("reject");
    setErrorMessage(null);
    try {
      await rejectRun(runId, { gate, by, reason: reason?.trim() || DEFAULT_REJECT_REASON });
      await onComplete?.();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Rejection failed");
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <button
          className="rounded-full bg-success px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
          disabled={pendingAction !== null}
          onClick={handleApprove}
          type="button"
        >
          {pendingAction === "approve" ? "Approving..." : "Approve"}
        </button>
        <button
          className="rounded-full bg-danger px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
          disabled={pendingAction !== null}
          onClick={handleReject}
          type="button"
        >
          {pendingAction === "reject" ? "Rejecting..." : "Reject"}
        </button>
      </div>
      {errorMessage ? <p className="text-xs text-danger">{errorMessage}</p> : null}
    </div>
  );
}
