"""Harness: the HUD ships its own typefaces (F-26).

The visible symptom was a 404 on Orbitron from `fonts.gstatic.com` — a Google
Fonts v2 asset hash that a cached stylesheet still pointed at, and Google had
retired the file. A hard reload fixed it. The titles were rendering in a
fallback, which is why the HUD looked wrong.

The symptom is trivial. What it exposed is not:

    dist\\  ->  zero .woff2, .woff or .ttf files

The typefaces were fetched from the network on every boot, from three places at
once — `index.html`'s `<link>`, and the same `@import` in BOTH `NotchView.scss`
and `SidecarView.scss`, so the built CSS carried it twice. A packaged build asked
Google for one stylesheet three times per load, and the HUD's identity — Orbitron
is *the* JARVIS display face — depended on a third-party CDN being reachable and
on that CDN not retiring an asset. It had retired one.

`ELECTRON_SHIP_PLAN.md` is next after the gate. A packaged desktop assistant that
renders in Times New Roman when the network is down, and phones a third party on
every launch, is not what "packaged" should mean. This was the HUD's last
external runtime dependency, and the fix belongs BEFORE packaging.

WHAT THIS PINS
--------------
Source AND build output. Checking the source alone would miss a CDN reference
that Vite inlines, and checking `dist/` alone passes on a stale build — so both,
and `dist/` only when it exists, since a clean checkout has not built yet.
"""

import glob
import io
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent / "jarvis-frontend"

_passed = 0
_failed = 0

CDN = ("fonts.googleapis.com", "fonts.gstatic.com")
FAMILIES = ("Orbitron", "Poppins")


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def _source_files():
    """Every source file that could reach for a font, minus the one that
    documents the finding."""
    out = []
    for pat in ("index.html", "src/**/*.css", "src/**/*.scss", "src/**/*.jsx",
                "src/**/*.js"):
        for f in glob.glob(str(FRONTEND / pat), recursive=True):
            if os.path.basename(f) == "fonts.css":
                continue      # its comment quotes the old URLs on purpose
            out.append(f)
    return out


# ── nothing asks the network for a typeface ───────────────────────────────────

def test_no_source_file_fetches_a_font_from_a_cdn():
    offenders = []
    for f in _source_files():
        src = _read(f)
        if any(host in src for host in CDN):
            offenders.append(os.path.relpath(f, FRONTEND))
    check(not offenders,
          "no source file references a font CDN"
          + (f" — OFFENDERS: {offenders}" if offenders else ""))


def test_the_duplicated_import_is_gone_from_both_view_stylesheets():
    """It was in both, so the built CSS carried it twice. Removing one would have
    left the fetch in place and looked like a fix."""
    for name in ("NotchView.scss", "SidecarView.scss"):
        src = _read(FRONTEND / "src" / name)
        check("@import url('https://fonts" not in src,
              f"{name} no longer imports a remote stylesheet")
        check("F-26" in src, f"{name} records why the import left")


def test_index_html_no_longer_preconnects_to_google():
    src = _read(FRONTEND / "index.html")
    check("preconnect" not in src or "gstatic" not in src,
          "the preconnect hints to Google are gone")
    check("F-26" in src, "...and the reason is in the file")


# ── the faces are declared once, locally ──────────────────────────────────────

def test_there_is_exactly_one_place_that_declares_the_faces():
    """Three declarations were the problem. One is the fix; two would be the
    problem again in a year."""
    declaring = [os.path.relpath(f, FRONTEND) for f in
                 glob.glob(str(FRONTEND / "src" / "**" / "*.css"), recursive=True)
                 + glob.glob(str(FRONTEND / "src" / "**" / "*.scss"), recursive=True)
                 if "@font-face" in _read(f)]
    check(declaring == ["src\\fonts.css"] or declaring == ["src/fonts.css"],
          f"only fonts.css declares @font-face (found {declaring})")


def test_it_is_imported_once_at_the_entry_point():
    src = _read(FRONTEND / "src" / "main.jsx")
    check(src.count('import "./fonts.css"') == 1, "imported exactly once")
    check(src.index('import "./fonts.css"') < src.index('import "./index.css"'),
          "...before the stylesheets that use the faces")


def test_every_weight_the_hud_uses_is_declared():
    """The old stylesheet requested Orbitron 400/700 and Poppins 300/400/500/600.
    A missing weight is a silent synthetic bold, which is the kind of thing that
    looks like a design choice."""
    css = _read(FRONTEND / "src" / "fonts.css")
    faces = re.findall(r"font-family:\s*\"([^\"]+)\";\s*font-style:\s*\w+;\s*"
                       r"font-weight:\s*([0-9 ]+);", css)
    have = {}
    for fam, wt in faces:
        have.setdefault(fam, set()).update(wt.split())
    check("400" in have.get("Orbitron", set()) and "700" in have.get("Orbitron", set()),
          f"Orbitron covers 400 and 700 ({sorted(have.get('Orbitron', []))})")
    for wt in ("300", "400", "500", "600"):
        check(wt in have.get("Poppins", set()), f"Poppins {wt} is declared")


def test_orbitron_ships_as_one_variable_file():
    """Google serves it as a variable font and the 400 and 700 downloads were
    byte-identical (md5 5d281085…), so one face covering the axis is correct and
    one fewer request."""
    css = _read(FRONTEND / "src" / "fonts.css")
    check(css.count("orbitron-") == 1, "one Orbitron file is referenced")
    check("font-weight: 400 700" in css, "...covering the weight range")


def test_every_referenced_file_actually_exists():
    """A @font-face pointing at a missing file fails exactly like the CDN 404
    did, and just as quietly."""
    css = _read(FRONTEND / "src" / "fonts.css")
    missing = [u for u in re.findall(r'url\("(/fonts/[^"]+)"\)', css)
               if not (FRONTEND / "public" / u.lstrip("/")).exists()]
    check(not missing, f"every declared file is in public/fonts ({missing or 'all present'})")


def test_font_display_swap_is_kept():
    """Text paints in the fallback and re-renders when the face is ready. With
    the files local that window is a frame or two, but on a cold Electron start
    it is still the difference between text and no text."""
    css = _read(FRONTEND / "src" / "fonts.css")
    # Counted inside the blocks, not across the file: the header comment explains
    # why swap is kept, and a comment is not a declaration.
    blocks = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
    with_swap = [b for b in blocks if "font-display: swap" in b]
    check(len(blocks) >= 9, f"there are {len(blocks)} faces")
    check(len(with_swap) == len(blocks),
          f"all {len(blocks)} faces set font-display: swap ({len(with_swap)})")


# ── and the build carries them ────────────────────────────────────────────────

def test_the_build_ships_the_font_files():
    """Checking the source alone would miss a reference Vite inlines; checking
    dist alone passes on a stale build. Skipped when nothing has been built,
    since a clean checkout is not a failure."""
    dist = FRONTEND / "dist"
    if not dist.exists():
        check(True, "dist/ not built — build check skipped")
        return
    fonts = glob.glob(str(dist / "**" / "*.woff2"), recursive=True)
    check(len(fonts) >= 9,
          f"the build ships the woff2 files ({len(fonts)} found)")


def test_the_build_asks_no_cdn_for_anything():
    dist = FRONTEND / "dist"
    if not dist.exists():
        check(True, "dist/ not built — build check skipped")
        return
    offenders = []
    for pat in ("**/*.html", "**/*.css", "**/*.js"):
        for f in glob.glob(str(dist / pat), recursive=True):
            if any(host in _read(f) for host in CDN):
                offenders.append(os.path.relpath(f, dist))
    check(not offenders,
          "the built output contains no font-CDN reference"
          + (f" — OFFENDERS: {offenders}" if offenders else ""))


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("HUD assets — F-26, the last external runtime dependency")
    print("=" * 62)
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            global _failed
            _failed += 1
            print(f"FAIL  {t.__name__} raised {type(e).__name__}: {e}")
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
