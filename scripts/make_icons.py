"""Erzeugt alle App-Icons aus einer einzigen Form (gelbe Karte, oben rechts gekappt, schwarze „7“ im 7-Schnitt).

Ausgabe in app/static/assets/:
  flip7live.ico            Windows: EXE, Taskleiste, Fenster (16–256 px)
  icon-<n>.png             Infobereich, Browser, Web-Manifest
  apple-touch-icon.png     Handy-Startbildschirm (180 px, deckender Hintergrund)
  favicon.svg              Browser-Tab (Vektor)

Aufruf: python scripts/make_icons.py   (nur nötig, wenn sich die Form ändert – Ergebnisse sind eingecheckt)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "assets"

YELLOW = "#f8d548"
CHARCOAL = "#101116"

# Form im Kartenraster 50 × 70 (Seitenverhältnis 5:7)
CARD = [(0, 0), (37, 0), (50, 13), (50, 70), (0, 70)]
SEVEN = [(10, 13), (40, 13), (40, 22), (26, 60), (15, 60), (28.5, 22), (10, 22)]


def _transform(points, scale, ox, oy):
    return [(ox + x * scale, oy + y * scale) for x, y in points]


def render(size: int, background: str | None = None, padding: float = 0.06) -> Image.Image:
    ss = 4  # Supersampling für saubere Kanten
    big = size * ss
    img = Image.new("RGBA", (big, big), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    usable = big * (1 - 2 * padding)
    scale = usable / 70
    ox = (big - 50 * scale) / 2
    oy = (big - 70 * scale) / 2
    draw.polygon(_transform(CARD, scale, ox, oy), fill=YELLOW)
    draw.polygon(_transform(SEVEN, scale, ox, oy), fill=CHARCOAL)
    return img.resize((size, size), Image.LANCZOS)


def svg() -> str:
    pts = lambda p: " ".join(f"{x:g},{y:g}" for x, y in p)  # noqa: E731
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 0 70 70">'
            f'<polygon points="{pts(CARD)}" fill="{YELLOW}"/><polygon points="{pts(SEVEN)}" fill="{CHARCOAL}"/></svg>\n')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 192, 256, 512]
    images = {s: render(s, padding=0.02 if s <= 32 else 0.06) for s in sizes}
    for s, im in images.items():
        im.save(OUT / f"icon-{s}.png")
    images[256].save(OUT / "flip7live.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    render(180, background=CHARCOAL, padding=0.12).convert("RGB").save(OUT / "apple-touch-icon.png")
    (OUT / "favicon.svg").write_text(svg(), encoding="utf-8")
    print("Icons erzeugt in", OUT)


if __name__ == "__main__":
    main()
