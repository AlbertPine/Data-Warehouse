"""Reproduce the dashboard's merchant/day filters and inspect source lineage."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warehouse import Warehouse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant", type=int, default=3828)
    parser.add_argument("--mmdd", default="0511")
    args = parser.parse_args()
    if len(args.mmdd) != 4 or not args.mmdd.isascii() or not args.mmdd.isdecimal():
        parser.error("日期用四位 MMDD")
    wh = Warehouse()
    db = wh.scalar("SELECT snapshot FROM tmall_control.runs WHERE scope='full' AND status='published' ORDER BY created DESC LIMIT 1")
    where = f"merchant_key={args.merchant} AND mmdd='{args.mmdd}'"
    ads = wh.rows(f"SELECT * FROM tmall_ads.merchant_day WHERE {where}")
    detail = wh.rows(f"""SELECT count() AS event_count,uniqExact(user_key) AS active_users,
        uniqExactIf(user_key,action_key=2) AS purchase_users FROM {db}.dwd_behavior WHERE {where}""")[0]
    assert detail == ({key:ads[0][key] for key in detail} if ads else dict.fromkeys(detail, 0))
    rows = wh.rows(f"""SELECT source_row,user_key,item_key,category_key,brand_key,action_key
        FROM {db}.dwd_behavior WHERE {where} ORDER BY source_row LIMIT 8""")
    print(json.dumps({"ads":ads,"dwd_totals":detail,"dwd_sample":rows},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
