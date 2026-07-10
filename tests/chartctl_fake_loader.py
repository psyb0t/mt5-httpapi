"""A Python stand-in for the loader EA, implementing the terminal side of
Chart Control Protocol v1. Lets the full endpoint suite run on Linux with
no MT5, no Windows — the same trick conftest uses to stub the SDK.

It reads desired.json and writes observed.json exactly as the MQL5 loader
would, so integration tests exercise the real registry merge + status
derivation against a realistic observed file.
"""
import json
import os


class FakeLoader:
    def __init__(self, protocol_dir: str):
        self.dir = protocol_dir
        os.makedirs(self.dir, exist_ok=True)
        os.makedirs(os.path.join(self.dir, "shots"), exist_ok=True)
        self.auto_trading = True
        self._next_chart_id = 133039100

    # ── protocol files ───────────────────────────────────────────
    def _read(self, name):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _write(self, name, obj):
        path = os.path.join(self.dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)

    # ── one reconcile pass ───────────────────────────────────────
    def reconcile(self, *, fail_ids=None, missing_ids=None):
        """Materialize observed.json from desired.json.

        fail_ids: deployments that should report a terminal error.
        missing_ids: enabled deployments the loader 'fails to attach'
                     (stay pending / degraded).
        """
        fail_ids = set(fail_ids or [])
        missing_ids = set(missing_ids or [])
        desired = self._read("desired.json") or {"revision": 0, "deployments": []}

        charts, dep_status, errors = [], [], []
        for dep in desired.get("deployments", []):
            did = dep["id"]
            if not dep.get("enabled", True):
                dep_status.append({"id": did, "status": "paused"})
                continue
            if did in fail_ids:
                errors.append({"id": did, "status": "failed",
                               "code": "EXPERT_NOT_ATTACHED",
                               "detail": "fake failure"})
                dep_status.append({"id": did, "status": "failed"})
                continue
            if did in missing_ids:
                dep_status.append({"id": did, "status": "pending"})
                continue
            cid = self._next_chart_id
            self._next_chart_id += 1
            charts.append({
                "chart_id": cid, "symbol": dep["symbol"],
                "timeframe": dep["timeframe"], "expert": dep["expert"],
                "expert_enabled": True, "deployment_id": did,
            })
            dep_status.append({"id": did, "status": "running", "chart_id": cid})

        observed = {
            "protocol": 1,
            "loader": {"name": "FakeLoader", "version": "1.0.0",
                       "last_loop": "now",
                       "applied_revision": desired.get("revision", 0)},
            "terminal": {"auto_trading": self.auto_trading},
            "charts": charts,
            "deployments": dep_status,
            "errors": errors,
        }
        self._write("observed.json", observed)
        return observed

    # ── command channel ──────────────────────────────────────────
    def handle_command(self):
        cmd = self._read("command.json")
        if not cmd:
            return None
        cid = cmd["command_id"]
        result = {"command_id": cid}
        if cmd.get("action") == "screenshot":
            fname = f"{cid}.png"
            with open(os.path.join(self.dir, "shots", fname), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")  # PNG magic, enough for a test
            result.update({"status": "ok", "file": fname})
        else:
            result.update({"status": "error", "error_code": "UNKNOWN_ACTION",
                           "error_detail": cmd.get("action", "")})
        self._write("command_result.json", result)
        try:
            os.remove(os.path.join(self.dir, "command.json"))
        except OSError:
            pass
        return result
