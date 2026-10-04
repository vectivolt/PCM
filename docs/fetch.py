"""Download the reference library again from docs/SOURCES.csv (the files themselves are not kept in git).

  python docs/fetch.py          # fetch every file marked OK that is missing, verify its SHA-256
  python docs/fetch.py --check  # only verify what is on disk
Rows marked MANUAL (sites that refuse scripted downloads) are listed at the end: save those by hand - see
"Still to download by hand" in docs/README.md. A hash mismatch means the maker has revised the document: check the
page references in gen/ and sim/ before relying on it. Exit code 1 if anything is missing or differs.
"""
import csv
import hashlib
import os
import sys
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
rows = list(csv.DictReader(open(os.path.join(REPO, "docs", "SOURCES.csv"), encoding="utf-8")))
bad, manual = [], [r for r in rows if r["status"] != "OK"]
for r in (r for r in rows if r["status"] == "OK"):
    path = os.path.join(REPO, r["path"])
    if not os.path.exists(path) and "--check" not in sys.argv:
        try:
            req = urllib.request.Request(r["url"], headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=120).read()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(data)
        except Exception as e:                      # refused, moved or offline: reported, never worked around
            bad.append("%s: %s" % (r["path"], e))
            continue
    if not os.path.exists(path):
        bad.append(r["path"] + ": missing")
    elif r["sha256"] and hashlib.sha256(open(path, "rb").read()).hexdigest() != r["sha256"]:
        bad.append(r["path"] + ": SHA-256 differs from the manifest")
print("%d files listed, %d to save by hand, %d problem(s)" % (len(rows), len(manual), len(bad)))
print("\n".join(bad + ["by hand: %s  <-  %s" % (r["path"], r["url"]) for r in manual]))
sys.exit(1 if bad else 0)
