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
    <div className="flex flex-col gap-3 rounded-2xl border border-border bg-panel px-4 py-3 text-sm text-muted md:flex-row md:items-center md:justify-between">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-xs uppercase tracking-[0.22em] text-muted-soft">
          {itemLabel}
        </p>
        <p className="text-sm text-copy">
          {from}-{to} of {pagination.total}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {onPageSizeChange ? (
          <label className="flex items-center gap-2 text-xs text-muted">
            <span>Page size</span>
            <select
              className="rounded-lg border border-border-strong bg-panel-strong/60 px-2 py-1.5 text-xs text-copy outline-none"
              value={pagination.limit}
              disabled={disabled}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
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
          type="button"
          disabled={!hasPrev || disabled}
          onClick={() => onPageChange(Math.max(0, pagination.offset - pagination.limit))}
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-copy/20 hover:text-copy disabled:cursor-not-allowed disabled:opacity-40"
        >
          Prev
        </button>
        <button
          type="button"
          disabled={!hasNext || disabled}
          onClick={() => onPageChange(pagination.offset + pagination.limit)}
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-copy/20 hover:text-copy disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
}
