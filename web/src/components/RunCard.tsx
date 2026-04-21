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
    <article className="rounded-[24px] border border-border bg-panel p-5 shadow-panel transition hover:border-border-strong hover:bg-panel-strong/80">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="font-mono text-sm text-copy">{ticket}</p>
          <p className="mt-2 text-sm leading-6 text-muted">{summary}</p>
        </div>
        <StatusBadge status={status} />
      </div>
      <div className="mt-4">
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
