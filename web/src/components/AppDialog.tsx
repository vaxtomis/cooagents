import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from "@headlessui/react";
import type { ReactNode } from "react";
import { X } from "lucide-react";

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
      <DialogBackdrop className="fixed inset-0 bg-copy/30" />
      <div className="fixed inset-0 overflow-y-auto p-3 sm:p-6">
        <div className="flex min-h-full items-start justify-center sm:items-center">
          <DialogPanel className="w-full max-w-4xl rounded-2xl border border-border bg-panel p-5 shadow-shell">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <DialogTitle className="font-serif text-2xl font-medium leading-tight text-copy">
                  {title}
                </DialogTitle>
                {description ? (
                  <p className="mt-1 text-sm leading-relaxed text-muted">{description}</p>
                ) : null}
              </div>
              <button
                aria-label="关闭弹窗"
                className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-border-strong bg-panel-strong/50 text-muted transition hover:border-copy/20 hover:text-copy"
                onClick={onClose}
                type="button"
              >
                <X className="size-4" strokeWidth={1.8} />
              </button>
            </div>
            <div className="mt-5">{children}</div>
          </DialogPanel>
        </div>
      </div>
    </Dialog>
  );
}
