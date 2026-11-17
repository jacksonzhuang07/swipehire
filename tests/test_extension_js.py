"""Extension JS invariants: syntax (node --check) + the safety/pacing
constants and dry-run wiring that must never silently regress."""
import shutil
import subprocess

import pytest

ROOT = __file__.rsplit("/tests/", 1)[0]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _src(name):
    with open(f"{ROOT}/extension/{name}") as f:
        return f.read()


@pytest.mark.parametrize("f", ["background.js", "content.js", "popup.js"])
def test_js_syntax(f):
    r = subprocess.run([NODE, "--check", f"{ROOT}/extension/{f}"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_pacing_invariants_present():
    bg = _src("background.js")
    assert "MIN_GAP_MS = 90 * 1000" in bg           # >=90s base gap, never weakened
    assert "BURST_MAX = 5" in bg                     # burst cap
    assert "linkedin: 30, indeed: 20, ats: 40" in bg  # daily caps
    assert "JITTER_MAX_MS" in bg and "Math.random" in bg  # randomized jitter on the gap
    assert "HALT_MS = 48 * 3600 * 1000" in bg        # 48h account-warning halt
    assert "overrideHalt" in bg                      # resumable only by explicit override


def test_dry_run_wiring_present():
    bg, ct, pp = _src("background.js"), _src("content.js"), _src("popup.js")
    assert "verified_dry_run" in bg and "verified_dry_run" in ct
    assert "dryRun" in bg and "dryRun" in ct and "dryRunToggle" in pp
    # dry-run finish must not consume pacing: the verified_dry_run branch
    # appears before any pace mutation in finishClaim's dry-run path
    i = bg.index('result.outcome === "verified_dry_run"')
    assert "pace.subs.push" not in bg[i:i + 700]


def test_safety_stops_present():
    ct = _src("content.js")
    for marker in ("recaptcha", "hcaptcha", "one-time-code", "accountWarning"):
        assert marker in ct
    assert "sh-submitclick" in ct and "submit_clicked" in _src("background.js")
    assert "answer-lookup" in ct or "sh-answer-lookup" in ct
