import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from "@headlessui/react";
import { X } from "lucide-react";
import type { ReactNode } from "react";

interface AppDialogProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
}

export function AppDialog({ open, title, description, onClose, children }: AppDialogProps) {
  return (
    <Dialog className="relative z-50" onClose={onClose} open={open}>
      <DialogBackdrop className="fixed inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="fixed inset-0 overflow-y-auto p-3 sm:p-6">
        <div className="flex min-h-full items-start justify-center sm:items-center">
          <DialogPanel className="relative w-full max-w-4xl overflow-hidden rounded-[30px] border border-border-strong bg-panel/98 p-5 shadow-shell">
            <div className="pointer-events-none absolute inset-[1px] rounded-[29px] border border-white/4" />
            <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(169,112,45,0.75),transparent)]" />

            <div className="relative flex items-start justify-between gap-4">
              <div className="min-w-0">
                <DialogTitle className="text-2xl font-semibold leading-tight tracking-[-0.03em] text-copy">
                  {title}
                </DialogTitle>
                {description ? (
                  <p className="mt-1 text-sm leading-relaxed text-muted">{description}</p>
                ) : null}
              </div>
              <button
                aria-label="关闭弹窗"
                className="inline-flex size-9 shrink-0 items-center justify-center rounded-[12px] border border-border bg-panel-deep text-muted transition hover:border-accent/40 hover:text-copy"
                onClick={onClose}
                type="button"
              >
                <X className="size-4" strokeWidth={1.8} />
              </button>
            </div>
            <div className="relative mt-5">{children}</div>
          </DialogPanel>
        </div>
      </div>
    </Dialog>
  );
}
