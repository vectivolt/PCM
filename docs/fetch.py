"""Reference library tool. The files under docs/datasheets and docs/reference-designs are other companies'
documents and are not kept in git; docs/SOURCES.csv lists each one with its URL and SHA-256.

  python docs/fetch.py            # download every listed file that is missing, verify its SHA-256
  python docs/fetch.py --check    # verify what is on disk, no network
  python docs/fetch.py --summary  # rewrite the generated blocks of docs/LIBRARY.md from the manifest

Requests are plain (no browser headers); a site that refuses is reported, never worked around. Rows marked MANUAL
are saved by hand ("Still to download by hand" in docs/LIBRARY.md). A hash mismatch means the maker has revised the
document: check the page references in gen/ and sim/ before relying on it. Exit code 1 on any problem.
"""
import collections
import csv
import hashlib
import os
import re
import sys
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS = os.path.join(REPO, "docs")
rows = list(csv.DictReader(open(os.path.join(DOCS, "SOURCES.csv"), encoding="utf-8", newline="")))


def check(fetch):
    bad = []
    for r in rows:
        path = os.path.join(REPO, r["path"])
        if r["status"] != "OK":
            if os.path.exists(path):
                bad.append(r["path"] + ": listed as MANUAL but on disk - set its row to OK with bytes and sha256")
            continue
        if not os.path.exists(path) and fetch:
            try:
                data = urllib.request.urlopen(r["url"], timeout=120).read()     # plain request, no browser headers
            except Exception as e:                  # refused, moved or offline: reported, never worked around
                bad.append("%s: %s" % (r["path"], e))
                continue
            if r["sha256"] and hashlib.sha256(data).hexdigest() != r["sha256"]:
                bad.append(r["path"] + ": the URL does not return the listed file (revised, or supplied by hand - see its note)")
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(data)
        if not os.path.exists(path):
            bad.append(r["path"] + ": missing")
        elif r["sha256"] and hashlib.sha256(open(path, "rb").read()).hexdigest() != r["sha256"]:
            bad.append(r["path"] + ": SHA-256 differs from the manifest")
    listed = collections.Counter(r["path"] for r in rows)
    bad += ["%s: %d rows in the manifest" % (p, n) for p, n in listed.items() if n > 1]
    for tree, depth in (("datasheets", 1), ("reference-designs", 2)):     # deeper files are unpacked archives
        base = os.path.join(DOCS, tree)
        for dp, _, names in os.walk(base):
            rel = os.path.relpath(dp, base)
            if rel == "." or rel.count(os.sep) + 1 != depth:
                continue
            for f in names:                         # our own notes (.md, .csv) are in git and need no row
                p = os.path.relpath(os.path.join(dp, f), REPO)
                if not f.endswith((".md", ".csv")) and f != ".DS_Store" and p not in listed:
                    bad.append(p + ": on disk but not in the manifest")
    return bad


def table(head, body, left=1):
    cell = lambda c: str(c).replace("|", "/").replace("\n", " ")
    return (["| " + " | ".join(head) + " |", "|" + "---|" * left + "---:|" * (len(head) - left)]
            + ["| " + " | ".join(cell(c) for c in r) + " |" for r in body] + [""])


def counts(sel):
    ok = [r for r in sel if r["status"] == "OK"]
    return [len(sel), len(ok), len(sel) - len(ok), "%.0f" % (sum(int(r["bytes"] or 0) for r in ok) / 1e6)]


def summary():
    ref = [r for r in rows if r["kind"] == "reference-design"]
    ds = [r for r in rows if r["kind"] == "datasheet"]
    by = lambda sel, key: sorted(collections.Counter(map(key, sel)))
    out = ["Counts from `docs/SOURCES.csv` (regenerate with `python docs/fetch.py --summary`).", ""]
    out += table(["", "rows", "on disk", "to fetch by hand", "MB on disk"],
                 [["reference designs"] + counts(ref), ["datasheets"] + counts(ds),
                  ["**total**"] + ["**%s**" % c for c in counts(rows)]])
    design = lambda r: "/".join(r["path"].split("/")[2:4])
    body = []
    for d in by(ref, design):
        readme = os.path.join(DOCS, "reference-designs", d, "README.md")
        title = open(readme, encoding="utf-8").readline().lstrip("# ").strip() if os.path.exists(readme) else ""
        body.append(["`%s`" % d, title.split(" - ", 1)[-1]] + counts([r for r in ref if design(r) == d])[1:3])
    out += ["### Reference designs", ""] + table(["design", "what it is", "on disk", "by hand"], body, left=2)
    cat = lambda r: r["path"].split("/")[2]
    out += ["### Datasheets per category", ""] + table(
        ["category", "rows", "on disk", "by hand", "MB"], [[c] + counts([r for r in ds if cat(r) == c]) for c in by(ds, cat)])
    hand = [r for r in rows if r["status"] != "OK"]
    todo = ["%d documents could not be fetched with a plain request; nothing was circumvented. Open the link in a browser, "
            "save the file at the path shown (relative to `docs/`), set its row to `OK` with `bytes` and `sha256`, and run "
            "`python docs/fetch.py --summary`." % len(hand), ""]
    for v in by(hand, lambda r: r["vendor"]):
        sel = [r for r in hand if r["vendor"] == v]
        todo += ["### %s (%d)" % (v, len(sel)), ""] + table(
            ["item", "save as", "link", "why"],
            [[r["title"], "`%s`" % r["path"].split("/", 1)[1], r["url"], r["note"]] for r in sel], left=4)
    path = os.path.join(DOCS, "LIBRARY.md")
    text = open(path, encoding="utf-8").read()
    for name, block in (("counts", out), ("by-hand", todo)):
        text, n = re.subn(r"(<!-- generated:%s -->\n).*?(<!-- /generated:%s -->)" % (name, name),
                          lambda m: m.group(1) + "\n".join(block) + m.group(2), text, flags=re.S)
        assert n == 1, "marker pair 'generated:%s' not found exactly once in docs/LIBRARY.md" % name
    open(path, "w", encoding="utf-8").write(text)
    print("docs/LIBRARY.md: %d rows, %d by hand" % (len(rows), len(hand)))


if __name__ == "__main__":
    if "--summary" in sys.argv:
        summary()
        sys.exit(0)
    bad = check(fetch="--check" not in sys.argv)
    manual = [r for r in rows if r["status"] != "OK"]
    print("%d files listed, %d to save by hand, %d problem(s)" % (len(rows), len(manual), len(bad)))
    print("\n".join(bad + ["by hand: %s  <-  %s" % (r["path"], r["url"]) for r in manual]))
    sys.exit(1 if bad else 0)
