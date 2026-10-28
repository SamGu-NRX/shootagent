import { Plus, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { openShootRoute } from "../App";
import { message, useStore } from "../state/store";

interface ViewDraft {
  key: number;
  brackets: File[];
  finished: File | null;
}

let nextKey = 1;
const emptyView = (): ViewDraft => ({ key: nextKey++, brackets: [], finished: null });

export function NewShootDialog({ onClose }: { onClose: () => void }) {
  const api = useStore((s) => s.api);
  const ref = useRef<HTMLDialogElement>(null);
  const [title, setTitle] = useState("");
  const [views, setViews] = useState<ViewDraft[]>([emptyView()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);

  const incomplete = views.findIndex((v) => v.brackets.length === 0 || !v.finished);
  const submit = async () => {
    if (busy) return;
    const root = ref.current;
    if (!title.trim()) {
      setError("Give the shoot a name.");
      root?.querySelector<HTMLInputElement>("input[name=title]")?.focus();
      return;
    }
    if (incomplete >= 0) {
      setError(`View ${incomplete + 1} needs at least one bracket and its finished photo.`);
      root?.querySelectorAll<HTMLElement>(".upload-view")[incomplete]?.querySelector("input")?.focus();
      return;
    }
    // docs/api.md: files named view<N>_bracket<M> and view<N>_finished. Indices are 0-based here; the
    // contract does not say, so this is listed as a gap in the web report.
    const form = new FormData();
    form.append("title", title.trim());
    views.forEach((v, n) => {
      v.brackets.forEach((f, m) => form.append(`view${n}_bracket${m}`, f, f.name));
      form.append(`view${n}_finished`, v.finished!, v.finished!.name);
    });
    setBusy(true);
    setError(null);
    try {
      const shoot = await api.uploadShoot(form);
      openShootRoute(shoot.id);
      onClose();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const update = (key: number, patch: Partial<ViewDraft>) =>
    setViews((vs) => vs.map((v) => (v.key === key ? { ...v, ...patch } : v)));

  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-labelledby="upload-title">
      <form
        method="dialog"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <header className="dialog-head">
          <h2 id="upload-title">Upload a shoot</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </header>
        <div className="dialog-body">
          <label className="field">
            <span>Shoot name</span>
            <input name="title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="412 Maple Court" autoFocus required />
          </label>
          <ol className="upload-views">
            {views.map((v, i) => (
              <li key={v.key} className="upload-view">
                <span className="upload-view-n num">View {i + 1}</span>
                <label className="file-field">
                  <span>Brackets</span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/tiff"
                    multiple
                    onChange={(e) => update(v.key, { brackets: [...(e.target.files ?? [])] })}
                  />
                  <em className="num">{v.brackets.length ? `${v.brackets.length} files` : "Choose files"}</em>
                </label>
                <label className="file-field">
                  <span>Finished photo</span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png"
                    onChange={(e) => update(v.key, { finished: e.target.files?.[0] ?? null })}
                  />
                  <em>{v.finished ? v.finished.name : "Choose file"}</em>
                </label>
                <button
                  type="button"
                  className="icon-btn"
                  aria-label={`Remove view ${i + 1}`}
                  disabled={views.length === 1}
                  onClick={() => setViews((vs) => vs.filter((x) => x.key !== v.key))}
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ol>
          <button type="button" className="btn btn-quiet" onClick={() => setViews((vs) => [...vs, emptyView()])}>
            <Plus size={14} aria-hidden /> Add view
          </button>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer className="dialog-foot">
          <span className="muted">
            {incomplete >= 0 ? `View ${incomplete + 1} needs brackets and a finished photo.` : `${views.length} views ready.`}
          </span>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy}>
            {busy ? "Uploading…" : "Create shoot"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
