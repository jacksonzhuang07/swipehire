"""Extension store-variant tests (Phase B; docs/STORE_LISTINGS.md).

extension/build_variants.py derives three store packages from the one
source tree. These tests pin the derivation contract:
- chrome.zip is the source tree untouched; edge.zip is BYTE-identical
  to it (Edge Add-ons takes the same Chromium MV3 package).
- firefox.zip differs ONLY in manifest.json: no service_worker key,
  background.scripts pointing at the real background file (which must
  itself be in the zip), and the stable Gecko add-on id.
- file lists are identical across all three variants.
- a source manifest whose shape is not exactly what the transform
  expects makes the build fail loudly instead of shipping a bad zip.
"""
import importlib.util, json, os, shutil, sys, zipfile
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension")

def _load_builder():
    spec = importlib.util.spec_from_file_location("build_variants", os.path.join(EXT_DIR, "build_variants.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

bv = _load_builder()

@pytest.fixture()
def dist(tmp_path):
    out = str(tmp_path / "dist")
    bv.build_all(EXT_DIR, out)
    return out

def _zip(dist, name):
    return zipfile.ZipFile(os.path.join(dist, name))

def _manifest(dist, name):
    return json.loads(_zip(dist, name).read("manifest.json"))

def test_file_lists_identical_across_variants(dist):
    lists = {v: _zip(dist, f"{v}.zip").namelist() for v in ("chrome", "edge", "firefox")}
    assert lists["chrome"] == lists["edge"] == lists["firefox"]
    assert "manifest.json" in lists["chrome"] and "background.js" in lists["chrome"]
    assert "build_variants.py" not in lists["chrome"]  # the builder is not product

def test_chrome_and_edge_zips_byte_identical(dist):
    with open(os.path.join(dist, "chrome.zip"), "rb") as f:
        chrome = f.read()
    with open(os.path.join(dist, "edge.zip"), "rb") as f:
        edge = f.read()
    assert chrome == edge

def test_chrome_manifest_is_the_source_manifest(dist):
    with open(os.path.join(EXT_DIR, "manifest.json"), "rb") as f:
        source = json.loads(f.read())
    assert _manifest(dist, "chrome.zip") == source
    assert _manifest(dist, "edge.zip") == source

def test_firefox_manifest_transform(dist):
    ff = _manifest(dist, "firefox.zip")
    assert ff["manifest_version"] == 3
    assert "service_worker" not in ff["background"]
    assert ff["background"] == {"scripts": ["background.js"]}
    assert ff["browser_specific_settings"]["gecko"]["id"] == "swipehire@swipehire.app"
    # the scripts entry must name a file that is actually in the zip
    names = _zip(dist, "firefox.zip").namelist()
    assert ff["background"]["scripts"][0] in names
    # everything except the background/bss keys is carried over unchanged
    with open(os.path.join(EXT_DIR, "manifest.json"), "rb") as f:
        source = json.loads(f.read())
    for key, value in source.items():
        if key in ("background", "browser_specific_settings"):
            continue
        assert ff[key] == value, key

def test_firefox_non_manifest_files_match_chrome(dist):
    cz, fz = _zip(dist, "chrome.zip"), _zip(dist, "firefox.zip")
    for name in cz.namelist():
        if name == "manifest.json":
            continue
        assert cz.read(name) == fz.read(name), name

def _copy_source(tmp_path):
    src = str(tmp_path / "ext")
    os.makedirs(src)
    for name in os.listdir(EXT_DIR):
        p = os.path.join(EXT_DIR, name)
        if os.path.isfile(p) and name != "build_variants.py":
            shutil.copy2(p, os.path.join(src, name))
    return src

def test_build_fails_loudly_on_unexpected_manifest_shape(tmp_path):
    src = _copy_source(tmp_path)
    mpath = os.path.join(src, "manifest.json")
    m = json.loads(open(mpath).read())
    m["background"] = {"service_worker": "background.js", "type": "module"}  # shape drift
    with open(mpath, "w") as f:
        json.dump(m, f)
    with pytest.raises(bv.VariantError):
        bv.build_all(src, str(tmp_path / "out"))
    assert not os.path.exists(str(tmp_path / "out"))  # nothing shipped

def test_build_fails_loudly_on_missing_background_file(tmp_path):
    src = _copy_source(tmp_path)
    os.remove(os.path.join(src, "background.js"))
    with pytest.raises(bv.VariantError):
        bv.build_all(src, str(tmp_path / "out"))
