import threading

from shootagent.tools import _ops


def test_prewarm_does_not_deadlock_when_the_analysis_finishes_at_once(monkeypatch):
    # A cached analysis returns immediately; its done-callback then runs on the submitting thread and
    # takes the same lock, which used to hang the QC run before it could reach run_end.
    monkeypatch.setattr(_ops, "_fn", lambda module, name: (lambda *a, **k: "cached"))
    done = threading.Event()

    def go():
        for _ in range(50):  # repeat so at least one job finishes before its callback is attached
            _ops._analysis_job(object(), "shoot", "v01").result(timeout=5)
        done.set()

    threading.Thread(target=go, daemon=True).start()
    assert done.wait(10), "prewarm deadlocked"
    assert ("shoot", "v01") not in _ops._qc_inflight
