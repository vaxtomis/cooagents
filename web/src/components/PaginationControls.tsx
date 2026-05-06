import type { Pagination } from "../types";

interface PaginationControlsProps {
  pagination: Pagination;
  itemLabel: string;
  onPageChange: (offset: number) => void;
  onPageSizeChange?: (limit: number) => void;
  pageSizeOptions?: number[];
  disabled?: boolean;
}

export function PaginationControls({
  pagination,
  itemLabel,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [6, 12, 24],
  disabled = false,
}: PaginationControlsProps) {
  const from = pagination.total === 0 ? 0 : pagination.offset + 1;
  const to = Math.min(pagination.offset + pagination.limit, pagination.total);
  const hasPrev = pagination.offset > 0;
  const hasNext = pagination.has_more;

  return (
    <div
      className="relative overflow-hidden rounded-[24px] border border-border bg-panel/90 px-4 py-3 text-sm text-muted shadow-panel md:flex md:items-center md:justify-between"
      data-pagination-tone="console"
    >
      <div className="pointer-events-none absolute inset-x-5 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(201,154,84,0.55),transparent)]" />

      <div className="relative flex flex-col gap-1 md:flex-row md:items-center md:gap-3">
        <p className="text-xs uppercase tracking-[0.24em] text-accent-soft">{itemLabel}</p>
        <p className="text-sm text-copy">
          {from}-{to} / 共 {pagination.total}
        </p>
      </div>

      <div className="relative mt-3 flex flex-wrap items-center gap-2 md:mt-0">
        {onPageSizeChange ? (
          <label className="flex items-center gap-2 text-xs text-muted">
            <span>每页</span>
            <select
              className="rounded-[12px] border border-border bg-panel-deep px-2.5 py-1.5 text-xs text-copy outline-none"
              disabled={disabled}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
              value={pagination.limit}
            >
              {pageSizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <button
          className="rounded-[12px] border border-border px-3 py-1.5 text-xs font-medium text-muted transition hover:border-accent/40 hover:bg-panel-strong/70 hover:text-copy disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!hasPrev || disabled}
          onClick={() => onPageChange(Math.max(0, pagination.offset - pagination.limit))}
          type="button"
        >
          上一页
        </button>
        <button
          className="rounded-[12px] border border-border px-3 py-1.5 text-xs font-medium text-muted transition hover:border-accent/40 hover:bg-panel-strong/70 hover:text-copy disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!hasNext || disabled}
          onClick={() => onPageChange(pagination.offset + pagination.limit)}
          type="button"
        >
          下一页
        </button>
      </div>
    </div>
  );
}
