"""
figma_parser.py — Figma API Extractor (Phase 3)
===============================================

Async extractor that pulls a Figma file via the REST API (httpx) and distils it into a
compact, code-generator-friendly set of design tokens: layout modes, spacing, dimensions,
typography, and colors.

Requires a Figma personal access token in FIGMA_TOKEN (or FIGMA_API_KEY).
All network I/O is async (httpx.AsyncClient) so it never blocks the J.A.R.V.I.S. loop.
"""

import os
import httpx

FIGMA_API = "https://api.figma.com/v1"


def _get_token(token: str | None = None) -> str:
    tok = token or os.getenv("FIGMA_TOKEN") or os.getenv("FIGMA_API_KEY")
    if not tok:
        raise RuntimeError("FIGMA_TOKEN (or FIGMA_API_KEY) is not set.")
    return tok


async def fetch_figma_file(file_key: str, token: str | None = None) -> dict:
    """Fetch the raw Figma document JSON for a file key."""
    headers = {"X-Figma-Token": _get_token(token)}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{FIGMA_API}/files/{file_key}", headers=headers)
        resp.raise_for_status()
        return resp.json()


def _rgba_to_hex(color: dict) -> str:
    r = round((color.get("r", 0) or 0) * 255)
    g = round((color.get("g", 0) or 0) * 255)
    b = round((color.get("b", 0) or 0) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def extract_design_tokens(figma_json: dict) -> dict:
    """
    Walk the document tree and collect design tokens.
    Returns a JSON-serialisable dict: layout, spacing, dimensions, typography, colors.
    """
    layout: list[dict] = []
    spacing: set[float] = set()
    dimensions: list[dict] = []
    typography: dict[str, dict] = {}   # keyed by a signature to dedupe
    colors: set[str] = set()

    def walk(node: dict):
        if not isinstance(node, dict):
            return

        name = node.get("name", "")

        # --- Auto-layout / layout mode ---
        mode = node.get("layoutMode")
        if mode and mode != "NONE":
            entry = {
                "node": name,
                "mode": mode,  # HORIZONTAL | VERTICAL
                "itemSpacing": node.get("itemSpacing"),
                "padding": {
                    "top": node.get("paddingTop"),
                    "right": node.get("paddingRight"),
                    "bottom": node.get("paddingBottom"),
                    "left": node.get("paddingLeft"),
                },
                "primaryAxisAlign": node.get("primaryAxisAlignItems"),
                "counterAxisAlign": node.get("counterAxisAlignItems"),
            }
            layout.append(entry)
            for v in (
                node.get("itemSpacing"),
                node.get("paddingTop"), node.get("paddingRight"),
                node.get("paddingBottom"), node.get("paddingLeft"),
            ):
                if isinstance(v, (int, float)) and v:
                    spacing.add(round(float(v), 2))

        # --- Dimensions ---
        bbox = node.get("absoluteBoundingBox")
        if isinstance(bbox, dict) and bbox.get("width") is not None:
            dimensions.append({
                "node": name,
                "width": round(bbox.get("width", 0), 2),
                "height": round(bbox.get("height", 0), 2),
            })

        # --- Typography ---
        style = node.get("style")
        if isinstance(style, dict) and style.get("fontSize"):
            sig = (
                f"{style.get('fontFamily')}|{style.get('fontWeight')}|"
                f"{style.get('fontSize')}|{style.get('lineHeightPx')}"
            )
            typography[sig] = {
                "fontFamily": style.get("fontFamily"),
                "fontWeight": style.get("fontWeight"),
                "fontSize": style.get("fontSize"),
                "lineHeightPx": style.get("lineHeightPx"),
                "letterSpacing": style.get("letterSpacing"),
                "textCase": style.get("textCase"),
            }

        # --- Colors (SOLID fills + strokes) ---
        for paint in (node.get("fills") or []) + (node.get("strokes") or []):
            if isinstance(paint, dict) and paint.get("type") == "SOLID" and paint.get("color"):
                colors.add(_rgba_to_hex(paint["color"]))

        for child in node.get("children", []) or []:
            walk(child)

    document = figma_json.get("document", {})
    walk(document)

    return {
        "name": figma_json.get("name"),
        "lastModified": figma_json.get("lastModified"),
        "layout": layout[:80],
        "spacing": sorted(spacing),
        "dimensions": dimensions[:80],
        "typography": list(typography.values()),
        "colors": sorted(colors),
    }


async def extract_from_figma(file_key: str, token: str | None = None) -> dict:
    """Convenience: fetch + extract in one async call."""
    data = await fetch_figma_file(file_key, token)
    return extract_design_tokens(data)
