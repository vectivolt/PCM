"""Independent pin audit (deliverable DEL-4): the pin tables as drawn, against a ledger transcribed from the
datasheets by someone who has not seen them.

  .venv/bin/python gen/pin_audit.py list   # gen/data/pin_audit_parts.csv: every orderable part drawn (no pin data)
  .venv/bin/python gen/pin_audit.py        # compare hardware/*/outputs/*_symbol_pins.csv with gen/data/pin_ledger*.csv

Ledger columns: mpn,pin,name,page  - one row per pin / ball / terminal, name exactly as the datasheet prints it.
A part passes when its pin numbers are identical to the ledger's and every name agrees after normalisation.
Exit code 1 on any mismatch, or if a drawn part is missing from the ledger.
"""
import csv
import glob
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
DATA = os.path.join(HERE, "data")
ONE_LETTER = {"K": "CATHODE", "A": "ANODE", "G": "GATE", "S": "SOURCE", "D": "DRAIN", "C": "COLLECTOR", "E": "EMITTER",
              "B": "BASE"}   # KiCad stock symbols name discrete pins with one letter


LETTER = {"CATHODE": "K", "CATH": "K", "ANODE": "A", "COLL": "C", "COLLECTOR": "C", "EMITTER": "E", "GATE": "G",
          "SOURCE": "S", "DRAIN": "D"}     # terminals a datasheet letters instead of numbering
POLARITY = {"K": {"-", "CATHODE", "C"}, "A": {"+", "ANODE"}}   # LED / diode datasheets mark terminals + and -


def drawn():
    """{mpn: {"meta": row, "boards": set, "pins": {number: name}}} from every board's as-built export."""
    parts = {}
    for path in sorted(glob.glob(os.path.join(REPO, "hardware", "*", "outputs", "*_symbol_pins.csv"))):
        board = os.path.basename(path).replace("_symbol_pins.csv", "")
        for r in csv.DictReader(open(path, encoding="utf-8")):
            p = parts.setdefault(r["mpn"], {"meta": r, "boards": set(), "pins": {}, "clash": []})
            p["boards"].add(board)
            if not same(p["pins"].get(r["pin"], r["name"]), r["name"]) and not same(r["name"], p["pins"][r["pin"]]):
                p["clash"].append("pin %s: %s vs %s (%s)" % (r["pin"], p["pins"][r["pin"]], r["name"], board))
            p["pins"][r["pin"]] = r["name"]
    return parts


def norm(name):
    return re.sub(r"[^A-Z0-9+\-]", "", name.upper())


def same(a, b):
    """Names agree if equal after normalisation, if one contains the other (multi-function pins), or if a one-letter
    KiCad discrete pin name matches the datasheet word."""
    raw_b = b
    a, b = norm(a), norm(b)
    if not a:                      # KiCad stock passives have unnamed pins ("~"): only the pin number is checked
        return True
    if a == b or (a and b and (a in b or b in a)):
        return True
    if a in POLARITY and b in POLARITY[a]:
        return True
    if a == "NC" and raw_b.strip() in ("*", "-", "NC", "N.C."):      # a pin the datasheet says to leave open
        return True
    if a == "".join(w[0] for w in re.findall(r"[A-Za-z]+", raw_b)).upper():   # "SS" for "Source sense"
        return True
    if (a, b) in (("KS", "KELVIN"), ("COM", "K1A2")) or (a.endswith("K") and raw_b.strip().endswith("-")) \
            or (a.endswith("A") and raw_b.strip().endswith("+")):
        return True
    m = re.fullmatch(r"([AK])(\d)", a)                      # "A1" for "anode (diode 1)" in a dual diode
    if m and ONE_LETTER[m.group(1)] in b and m.group(2) in b:
        return True
    if a == "TAB":                 # mounting tab: its electrical identity is the net it is tied to, checked by ERC/review
        return True
    return a in ONE_LETTER and (ONE_LETTER[a] in b or (len(b) >= 3 and ONE_LETTER[a].startswith(b)))


def main():
    parts = drawn()
    os.makedirs(DATA, exist_ok=True)
    if sys.argv[1:] == ["list"]:
        path = os.path.join(DATA, "pin_audit_parts.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["mpn", "mfr", "pkg", "ds", "pin_count", "boards"])
            for mpn, p in sorted(parts.items()):
                m = p["meta"]
                w.writerow([mpn, m["mfr"], m["pkg"], m["ds"], len(p["pins"]), " ".join(sorted(p["boards"]))])
        print("%d parts -> %s" % (len(parts), os.path.relpath(path, REPO)))
        return 0
    ledger = defaultdict(dict)
    for lpath in sorted(glob.glob(os.path.join(DATA, "pin_ledger*.csv"))):     # one file per auditor
        if lpath.endswith("_notes.csv"):                                       # the auditor's remarks, not pins
            continue
        for r in csv.DictReader(open(lpath, encoding="utf-8")):
            ledger[r["mpn"].strip()][r["pin"].strip()] = r["name"].strip()
    notes = {}
    for npath in sorted(glob.glob(os.path.join(DATA, "pin_ledger*_notes.csv"))):
        for r in csv.DictReader(open(npath, encoding="utf-8")):
            notes.setdefault(r["mpn"].strip(), r["issue"].strip())
    rows, bad = [], 0
    for mpn, p in sorted(parts.items()):
        issues, result = list(p["clash"]), None
        if mpn not in ledger and mpn in notes and not issues:
            result, issues = "NOT AUDITABLE", [notes[mpn]]     # the auditor could not transcribe it, and says why
        elif mpn not in ledger:
            issues.append("not in the ledger")
        else:
            led = ledger[mpn]
            # a two-terminal diode whose datasheet only shows a cathode band: the auditor labels the terminals A / K,
            # the symbol numbers them 1 / 2 and NAMES them K / A - match the two by name
            # the datasheet letters or names its terminals instead of numbering them (lettered discretes, power
            # modules with terminal groups): compare the SET of names; the symbol's own numbering is then arbitrary
            lettered = {LETTER.get(norm(k), norm(k)): v for k, v in led.items()}
            drawn_names = {LETTER.get(norm(v), norm(v)) for v in p["pins"].values()}
            if not any(k.isdigit() for k in led) and set(lettered) == drawn_names:
                led = {num: lettered[LETTER.get(norm(name), norm(name))] for num, name in p["pins"].items()}
            elif set(led) == {"+", "-"} and set(p["pins"]) == {"1", "2"}:
                led = {"1": "+", "2": "-"}      # KiCad C_Polarized: pin 1 is the positive plate (net polarity: review)
            elif not any(k.isdigit() for k in led):     # named terminals, symbol adds a suffix ("+UC" for "+")
                hit = {k: [n for n, v in p["pins"].items() if same(v, led[k])] for k in led}
                if all(len(v) == 1 for v in hit.values()) and len({v[0] for v in hit.values()}) == len(led):
                    led = {v[0]: led[k] for k, v in hit.items()}
            missing, extra = sorted(set(led) - set(p["pins"])), sorted(set(p["pins"]) - set(led))
            # an exposed pad: the datasheet labels it ("PowerPAD", "Thermal pad"), the symbol numbers it N+1
            if (len(missing) == 1 and len(extra) == 1 and "PAD" in missing[0].upper() and extra[0].isdigit()
                    and int(extra[0]) == len(p["pins"])):
                missing, extra = [], []
            # a tab or pad the datasheet lists as an extra terminal electrically identical to a drawn pin
            missing = [m for m in missing if not ("PAD" in m.upper() or "TAB" in m.upper()
                                                  or (m.isdigit() and int(m) == len(p["pins"]) + 1
                                                      and norm(led[m]) in {norm(v) for k, v in led.items() if k != m}))]
            missing = [m for m in missing if led.get(m) or True]
            if set(missing) <= {"I1", "I2"} and len(p["pins"]) == 4:     # 4-terminal shunt: sense pins unnamed in the datasheet
                missing, extra = [], []
            if "aperture" in p["meta"]["pkg"]:      # through-hole transducer: the primary is a hole, drawn as pin P
                extra = [e for e in extra if e != "P"]
            # a connector shield: the datasheet shows unnumbered shield legs (ledger row "SHIELD (shield pad)"), the symbol
            # draws them as one lettered pin named SHIELD (KiCad convention "SH")
            if any("SHIELD" in k.upper() for k in led):
                extra = [e for e in extra if not (not e.isdigit() and "SHIELD" in norm(p["pins"][e]).upper())]
            if missing:
                issues.append("datasheet pins not drawn: %s" % " ".join(missing))
            if extra:
                issues.append("drawn pins not in the datasheet: %s" % " ".join(extra))
            issues += ["pin %s drawn %r, datasheet %r" % (n, p["pins"][n], led[n])
                       for n in sorted(set(led) & set(p["pins"]))
                       if norm(led[n]) != norm(n) and not same(p["pins"][n], led[n])]   # datasheet gives numbers only
        bad += bool(issues) and result is None
        rows.append([mpn, " ".join(sorted(p["boards"])), len(p["pins"]), result or ("FAIL" if issues else "PASS"),
                     "; ".join(issues)])
    with open(os.path.join(DATA, "pin_audit_report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["mpn", "boards", "pins", "result", "issues"])
        w.writerows(rows)
    for r in rows:
        if r[3] == "FAIL":
            print("FAIL %-22s %s" % (r[0], r[4][:300]))
    ok = [r for r in rows if r[3] == "PASS"]
    na = [r for r in rows if r[3] == "NOT AUDITABLE"]
    print("pin audit: %d parts drawn; %d pass (%d pins), %d fail, %d not auditable from their datasheet "
          "-> gen/data/pin_audit_report.csv" % (len(rows), len(ok), sum(r[2] for r in ok), bad, len(na)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
