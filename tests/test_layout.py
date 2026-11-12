"""Layout-regression checks with headless Chromium (Playwright).

These exist because SwipeHire v2 shipped with a broken card layout that the
API test suite could not see: the deck card's children were scattered and
overlapping the nav. The browser renders the real page (logged in as the
demo candidate) and asserts geometric invariants:

  * no horizontal page overflow (scrollWidth <= innerWidth)
  * the swipe card is inside the viewport and at least 300px wide
  * the card does not overlap the nav bar
  * the "Why this job" box is at least 60% of the card's width

Skipped gracefully when Playwright or its Chromium binary is unavailable.
"""
import os, sys, threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402
from app import create_app  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server
    db = str(tmp_path_factory.mktemp("layout") / "layout.db")
    app = create_app(db_path=db, testing=True)
    srv = make_server("127.0.0.1", 0, app)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception:
        import glob
        for exe in sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"))):
            return p.chromium.launch(executable_path=exe)
        raise


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_playwright() as p:
            try:
                b = _launch(p)
            except Exception as e:
                pytest.skip(f"Chromium unavailable: {e}")
            yield b
            b.close()
    except Exception as e:
        pytest.skip(f"Playwright unavailable: {e}")


def _discovery_page(browser, base, width, height):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.goto(base + "/", wait_until="networkidle")
    page.wait_for_selector("#li-email")
    page.evaluate("demoLogin('demo@swipehire.app')")
    page.wait_for_selector("#top-card")
    page.wait_for_timeout(400)
    return ctx, page


def _measure(page):
    return page.evaluate("""() => {
      const card = document.querySelector('#top-card');
      const why = card ? card.querySelector('.why') : null;
      const nav = document.querySelector('#main-nav');
      const r = el => { if (!el) return null; const b = el.getBoundingClientRect();
        return {top: b.top, bottom: b.bottom, left: b.left, right: b.right, width: b.width}; };
      const cr = r(card), nr = r(nav);
      const overlap = (cr && nr) ? !(cr.bottom <= nr.top || nr.bottom <= cr.top || cr.right <= nr.left || nr.right <= cr.left) : null;
      return { innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth,
               card: cr, nav: nr, overlap,
               whyRatio: (cr && why) ? why.getBoundingClientRect().width / cr.width : null };
    }""")


@pytest.mark.parametrize("width,height", [(1920, 1080), (390, 844)])
def test_discovery_layout(browser, server, width, height):
    ctx, page = _discovery_page(browser, server, width, height)
    try:
        m = _measure(page)
        assert m["scrollWidth"] <= m["innerWidth"], f"horizontal overflow: {m}"
        assert m["card"] is not None
        assert m["card"]["width"] >= 300, f"card too narrow: {m['card']}"
        assert m["card"]["left"] >= 0 and m["card"]["right"] <= m["innerWidth"] + 1, f"card off-viewport: {m['card']}"
        assert m["overlap"] is False, f"card overlaps nav: card={m['card']} nav={m['nav']}"
        assert m["whyRatio"] is not None and m["whyRatio"] >= 0.6, f"why box too narrow: ratio={m['whyRatio']}"
    finally:
        ctx.close()
