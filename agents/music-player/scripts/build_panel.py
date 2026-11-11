"""Refresh the web panel in res/, which the official Studio builder packages."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
output = ROOT / "res/music_panel.zip"
with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
    for name in ("index.html", "player.css", "player.js"):
        entry = ZipInfo("dist/" + name)
        entry.compress_type = ZIP_DEFLATED
        entry.create_system = 3
        entry.external_attr = 0o100644 << 16
        bundle.writestr(entry, (ROOT / "web" / name).read_bytes())
print(f"Updated {output}")
