"""Operating-temperature audit for PV-20 (ambient -30..+60 C): every ORDERABLE/RFQ part with a datasheet in the three
module BOMs against gen/data/temp_audit.csv.   .venv/bin/python gen/temp_audit.py
Cold limit -30 C; hot limit 85 C (enclosure air is hotter than the 60 C inlet). Exit code 1 if a part has no audit row."""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COLD, HOT = -30, 85

parts = {}    # (manufacturer, mpn) -> [modules, boards]; a BOM line with no MPN is keyed on its Value
for module in ("PV-P75", "PV-P100-110", "PCS-P125", "PV-P75-FULL", "PV-P100-110-FULL", "DAB-D60-FULL"):
    for r in csv.DictReader(open(os.path.join(HERE, "..", "bom", module + "_module_BOM.csv"), newline="")):
        if r["Sourcing"] in ("ORDERABLE", "RFQ") and r["Datasheet"].strip():
            use = parts.setdefault((r["Manufacturer"], r["MPN"] or r["Value"]), [set(), set()])
            use[0].add(module)
            use[1].update(u.split(":")[0].strip() for u in r["Used on"].split(";"))
audit = {(r["mfr"], r["mpn"]): r for r in csv.DictReader(open(os.path.join(HERE, "data", "temp_audit.csv"), newline=""))}

missing = sorted(k for k in parts if k not in audit)
unknown = sorted(k for k in parts if k in audit and "" in (audit[k]["t_min_C"], audit[k]["t_max_C"]))
known = [k for k in sorted(parts) if k in audit and k not in unknown]
cold = [k for k in known if float(audit[k]["t_min_C"]) > COLD]
hot = [k for k in known if float(audit[k]["t_max_C"]) < HOT]
for title, group in (("(a) no audit row", missing), ("(b) t_min above %d C or unknown" % COLD, unknown + cold),
                     ("(c) t_max below %d C" % HOT, hot)):
    print(title)
    for k in group:
        a = audit.get(k, {})
        print("  %s [%s] %s..%s C %s p%s | %s | %s" % (k[1], k[0], a.get("t_min_C", ""), a.get("t_max_C", ""), a.get("basis", ""),
              a.get("page", ""), ",".join(sorted(parts[k][0])), ",".join(sorted(parts[k][1]))))
ok = len(parts) - len(set(missing + unknown + cold + hot))
print("temp audit: %d parts; %d ok; %d cold-limited; %d hot-limited; %d unknown; %d not audited"
      % (len(parts), ok, len(cold), len(hot), len(unknown), len(missing)))
sys.exit(1 if missing else 0)
