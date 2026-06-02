"""Parse apple_music_library.xml and list every playlist with size + sample tracks."""
from __future__ import annotations
import plistlib
from pathlib import Path

XML = Path(r"C:\Users\Branden\OneDrive\Documents\Music Enrichment\Music-Enrichment\inputs\apple_music_library.xml")

with XML.open("rb") as f:
    plist = plistlib.load(f)

tracks = plist.get("Tracks", {})  # keyed by track id (str)
playlists = plist.get("Playlists", [])

print(f"Tracks in XML: {len(tracks)}")
print(f"Playlists in XML: {len(playlists)}")
print()

rows = []
for pl in playlists:
    name = pl.get("Name", "<unnamed>")
    items = pl.get("Playlist Items") or []
    smart = "Smart Info" in pl or "Smart Criteria" in pl
    distinguished = pl.get("Distinguished Kind")  # 2=movies, 19=audiobooks, etc. - skip system
    if distinguished is not None:
        continue
    rows.append((name, len(items), smart, items))

rows.sort(key=lambda r: -r[1])

for name, count, smart, items in rows:
    tag = " [SMART]" if smart else ""
    print(f"{count:5d}  {name}{tag}")
    if count and count <= 8:
        for it in items[:8]:
            tid = str(it.get("Track ID"))
            tr = tracks.get(tid, {})
            print(f"           - {tr.get('Artist', '?')} - {tr.get('Name', '?')}")
    elif count:
        for it in items[:3]:
            tid = str(it.get("Track ID"))
            tr = tracks.get(tid, {})
            print(f"           - {tr.get('Artist', '?')} - {tr.get('Name', '?')}")
        print(f"           ... ({count-3} more)")
