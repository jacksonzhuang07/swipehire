#!/usr/bin/env python3
"""Build distributable store zips from the single extension source tree.

Outputs (default: extension/dist/):
  chrome.zip   — the source tree as-is (Chrome Web Store / load-unpacked
                 equivalent). File set matches the zip the app itself
                 serves at /extension/download.
  edge.zip     — byte-identical to chrome.zip: Microsoft Edge Add-ons
                 accepts the same Chromium MV3 package unchanged.
  firefox.zip  — the same files, but manifest.json is transformed for
                 Firefox (see firefox_manifest): the MV3 service worker
                 becomes an event page (background.scripts, same file)
                 and a stable Gecko add-on id is added.

The source manifest's shape is validated BEFORE anything is written,
and the script fails loudly (exit 1, no zips) if it is not exactly what
the transform expects — a silently-wrong store package is worse than
none. Stdlib only, like the rest of this project.

Usage: python3 extension/build_variants.py [--dist DIR]
"""
import argparse, copy, json, os, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
GECKO_ID = "swipehire@swipehire.app"
BACKGROUND_FILE = "background.js"
# Fixed zip timestamps: chrome.zip and edge.zip must come out
# byte-identical, and rebuilds should be reproducible.
ZIP_DATE = (2020, 1, 1, 0, 0, 0)


class VariantError(Exception):
    pass


def package_files(source_dir):
    """The distributable file set: every regular file in the source
    tree except this builder, build output, caches, and stray zips."""
    out = []
    for name in sorted(os.listdir(source_dir)):
        p = os.path.join(source_dir, name)
        if not os.path.isfile(p):
            continue
        if name == os.path.basename(__file__) or name.endswith(".zip"):
            continue
        if name.startswith("."):
            continue
        out.append(name)
    return out


def load_manifest(source_dir):
    path = os.path.join(source_dir, "manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise VariantError(f"cannot read/parse manifest.json: {e}")


def validate_source_manifest(manifest, files):
    """Fail loudly unless the source manifest is exactly the shape the
    Firefox transform (and the stores) expect."""
    if manifest.get("manifest_version") != 3:
        raise VariantError(f"manifest_version must be 3, got {manifest.get('manifest_version')!r}")
    bg = manifest.get("background")
    if bg != {"service_worker": BACKGROUND_FILE}:
        raise VariantError(
            "background must be exactly "
            f"{{'service_worker': '{BACKGROUND_FILE}'}}, got {bg!r} — "
            "the Firefox transform replaces this one key; if the source "
            "manifest changed shape, update the transform deliberately.")
    if BACKGROUND_FILE not in files:
        raise VariantError(f"{BACKGROUND_FILE} missing from the package files")
    if "browser_specific_settings" in manifest:
        raise VariantError("source manifest already has browser_specific_settings — "
                           "it belongs only in the derived Firefox variant")
    for key in ("name", "version", "description"):
        if not manifest.get(key):
            raise VariantError(f"manifest is missing {key!r} (stores require it)")


def firefox_manifest(manifest):
    """Derive the Firefox manifest from the (validated) Chrome one.

    - background.service_worker → background.scripts: Firefox MV3 runs
      the background as an EVENT PAGE, not a service worker. Same file,
      same code — background.js is written in a worker-agnostic way
      (all state in chrome.storage, no service-worker-only APIs); see
      docs/STORE_LISTINGS.md for the compatibility findings.
    - browser_specific_settings.gecko.id: a stable id so AMO signing
      and updates keep one identity across versions.
    - data_collection_permissions is deliberately NOT set here: it is
      optional in the manifest, and AMO collects the data-collection
      declaration during submission — the packet in
      docs/STORE_LISTINGS.md tells the owner exactly what to declare
      (the add-on DOES transmit application data to the SwipeHire API).
    Everything else is carried over unchanged.
    """
    m = copy.deepcopy(manifest)
    worker = m["background"]["service_worker"]
    m["background"] = {"scripts": [worker]}
    m["browser_specific_settings"] = {"gecko": {"id": GECKO_ID}}
    return m


def _write_zip(path, entries):
    """entries: list of (arcname, bytes). Deterministic output."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for arcname, data in entries:
            info = zipfile.ZipInfo(arcname, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def build_all(source_dir=HERE, dist_dir=None):
    dist_dir = dist_dir or os.path.join(source_dir, "dist")
    files = package_files(source_dir)
    manifest = load_manifest(source_dir)
    validate_source_manifest(manifest, files)  # raises before any write
    raw = {}
    for name in files:
        with open(os.path.join(source_dir, name), "rb") as f:
            raw[name] = f.read()
    chrome_entries = [(name, raw[name]) for name in files]
    ff = firefox_manifest(manifest)
    ff_bytes = (json.dumps(ff, indent=2, sort_keys=True) + "\n").encode("utf-8")
    firefox_entries = [(name, ff_bytes if name == "manifest.json" else raw[name])
                       for name in files]
    os.makedirs(dist_dir, exist_ok=True)
    paths = {}
    for variant, entries in (("chrome", chrome_entries),
                             ("edge", chrome_entries),
                             ("firefox", firefox_entries)):
        p = os.path.join(dist_dir, f"{variant}.zip")
        _write_zip(p, entries)
        paths[variant] = p
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default=None, help="output directory (default: extension/dist/)")
    args = ap.parse_args(argv)
    try:
        paths = build_all(HERE, args.dist)
    except VariantError as e:
        print(f"build_variants: REFUSING to build: {e}", file=sys.stderr)
        return 1
    for variant, p in paths.items():
        print(f"{variant}: {p} ({os.path.getsize(p)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
