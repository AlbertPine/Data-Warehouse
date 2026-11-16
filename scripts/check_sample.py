"""Independently recompute the two-day KPI directly from raw CSV."""
import csv
import json
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "天猫电商用户行为与复购数据/data_format1/data_format1"
active, purchase = defaultdict(set), defaultdict(set)
days = Counter()
with (source / "user_log_format1.csv").open(encoding="utf-8-sig",newline="") as stream:
    rows = csv.reader(stream)
    next(rows)
    for user, item, category, merchant, brand, day, action in rows:
        if day in ("0511", "0512"):
            days[day] += 1
            active[int(merchant)].add(int(user))
            if action == "2":
                purchase[int(merchant)].add(int(user))
sample_path = ROOT / "outputs/sample.json"
evidence = json.loads(sample_path.read_text(encoding="utf-8"))
top = sorted(active, key=lambda m: (-len(active[m]), m))[:5]
assert [m["merchant_key"] for m in evidence["top_merchants"]] == top
assert sum(days.values()) == evidence["counts"]["dwd_behavior"]
for row in evidence["top_merchants"]:
    merchant = row["merchant_key"]
    assert row["active_users"] == len(active[merchant])
    assert row["purchase_users"] == len(purchase[merchant])
assert evidence["counts"]["ads_merchant_period"] == len(active)
with (source/"train_format1.csv").open(encoding="utf-8-sig",newline="") as stream:
    labels = Counter(r["label"] for r in csv.DictReader(stream))
assert evidence["repeat_sample"] == {"labeled_pairs":sum(labels.values()),"positive_pairs":labels["1"]}
checked = {"daily_source_rows": dict(days), "merchants":len(active), "top5_exact_match":True,
           "label_baseline_exact_match":True, "batch":evidence["batch"]}
(ROOT / "outputs/sample_check.json").write_text(json.dumps(checked,indent=2),encoding="utf-8")
print(json.dumps(checked,indent=2))
