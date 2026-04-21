import { StageProgress } from "./StageProgress";
import { StatusBadge } from "./StatusBadge";

export function RunCard({
  ticket,
  summary,
  stage,
  status,
  failedAtStage,
  onClick,
}: {
  ticket: string;
  summary: string;
  stage: string;
  status: string;
  failedAtStage?: string | null;
  onClick?: () => void;
}) {
  const content = (
    <article className="rounded-2xl border border-border bg-panel p-5 shadow-whisper transition hover:shadow-[0_0_0_1px_var(--color-ring-warm),0_12px_32px_rgba(20,20,19,0.06)]">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-[13px] font-medium tracking-tight text-copy">
            {ticket}
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted">{summary}</p>
        </div>
        <StatusBadge status={status} />
      </div>
      <div className="mt-5">
        <StageProgress failedAtStage={failedAtStage} stage={stage} />
      </div>
    </article>
  );

  if (!onClick) {
    return content;
  }

  return (
    <button className="block w-full text-left" onClick={onClick} type="button">
      {content}
    </button>
  );
}
