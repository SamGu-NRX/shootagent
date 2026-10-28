import { X } from "lucide-react";
import { useStore } from "../state/store";

export function Toast() {
  const toast = useStore((s) => s.toast);
  const dismiss = useStore((s) => s.dismissToast);
  const error = toast?.tone === "error";
  return (
    <>
      <div className="toast-region" role="status" aria-live="polite">
        {toast && !error && (
          <div key={toast.text} className="toast">
            {toast.text}
          </div>
        )}
      </div>
      <div className="toast-region" role="alert">
        {toast && error && (
          <div key={toast.text} className="toast" data-tone="error">
            <span>{toast.text}</span>
            <button className="toast-close" onClick={dismiss} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        )}
      </div>
    </>
  );
}
