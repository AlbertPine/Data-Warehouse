"""Check full ADS against detail and emit a reproducible KPI scorecard."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warehouse import ROOT, Warehouse


def benchmark(wh, queries):
    observations = {key: [] for key in queries}
    answers = {}
    for turn in range(4):
        order = list(queries) if turn % 2 == 0 else list(reversed(queries))
        for key in order:
            before = time.perf_counter()
            answers[key] = wh.rows(queries[key])
            elapsed = round((time.perf_counter() - before) * 1000, 1)
            if turn:
                observations[key].append(elapsed)
    assert answers["detail"] == answers["summary"]
    return {key: {"samples_ms": values, "median_ms": statistics.median(values)}
            for key, values in observations.items()}


def score(name, numerator, denominator, prior, target, period):
    value = None if not denominator else numerator / denominator
    return dict(name=name, period=period, numerator=numerator, denominator=denominator,
                value=value, target=target, prior=prior,
                status=("未设定" if target is None else "无数据" if value is None
                        else "达到" if value >= target else "未达到"),
                change=None if value is None or prior is None else value - prior)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant", type=int, default=3828)
    parser.add_argument("--targets", type=Path, help="JSON with optional active, purchase, return7 and repeat targets")
    args = parser.parse_args()
    targets = json.loads(args.targets.read_text(encoding="utf-8")) if args.targets else {}
    for name, target in targets.items():
        if name not in {"active", "purchase", "return7", "repeat"} or type(target) not in (int, float):
            parser.error("目标必须是有效指标名和数字")
        if not 0 <= target <= (10**9 if name == "active" else 1):
            parser.error("人数目标必须非负，比例目标使用 0–1")
    merchant = args.merchant
    wh = Warehouse()
    db = wh.scalar("SELECT snapshot FROM tmall_control.runs WHERE scope='full' AND status='published' ORDER BY created DESC LIMIT 1")
    where = f"merchant_key={merchant}"
    checks = []
    for day in (1, 178):
        direct = wh.rows(f"""SELECT countIf(first=1) AS eligible_users,
            countIf(first=1 AND later=1) AS returned_users FROM (
                SELECT user_key,max(day_seq={day}) first,max(day_seq>{day}) later
                FROM {db}.dwd_behavior WHERE {where} AND day_seq BETWEEN {day} AND {day+7}
                GROUP BY user_key)""")[0]
        summary = wh.rows(f"""SELECT sum(eligible_users) AS eligible_users,
            sum(returned_users) AS returned_users FROM {db}.dws_merchant_return_7d
            WHERE {where} AND day_seq={day}""")
        assert direct == summary[0], (day, direct, summary[0])
        checks.append(dict(day_seq=day, **direct))
    assert wh.scalar(f"SELECT max(day_seq) FROM {db}.dws_merchant_return_7d") == "178"
    assert wh.scalar(f"SELECT count() FROM {db}.dws_merchant_return_7d WHERE returned_users>eligible_users") == "0"
    quality = wh.rows(f"""SELECT uniqExactIf(item_key, categories>1) AS multi_category_items,
        uniqExactIf(item_key, brands>1) AS multi_brand_items FROM (
          SELECT item_key, uniqExact(category_key) categories,
          uniqExactIf(brand_key,brand_key!=0) brands FROM {db}.dwd_behavior GROUP BY item_key)""")[0]
    quality["unknown_brand_events"] = int(wh.scalar(f"SELECT count() FROM {db}.dwd_behavior WHERE brand_key=0"))
    def period(first, last):
        return wh.rows(f"""SELECT uniqExact(user_key) AS active,
            uniqExactIf(user_key,purchased) AS purchase FROM {db}.dws_merchant_user_day
            WHERE {where} AND day_seq BETWEEN {first} AND {last}""")[0]
    cur, prev = period(155, 184), period(125, 154)
    def return_period(first, last):
        return wh.rows(f"""SELECT sum(eligible_users) eligible, sum(returned_users) returned
            FROM {db}.dws_merchant_return_7d WHERE {where} AND day_seq BETWEEN {first} AND {last}""")[0]
    ret, prev_ret = return_period(149, 178), return_period(119, 148)
    lab = wh.rows(f"SELECT labeled_pairs,positive_pairs FROM {db}.ads_repeat_sample WHERE {where}")
    lab = (lab or [{"positive_pairs": 0, "labeled_pairs": 0}])[0]
    ratio = lambda x, y: x / y if y else None
    items = [
        {**score("期间活跃用户数", cur["active"], 1, prev["active"], targets.get("active"), "1012–1110"),
         "denominator": None},
        score("期间购买用户占比", cur["purchase"], cur["active"], ratio(prev["purchase"], prev["active"]),
              targets.get("purchase"), "1012–1110"),
        score("7 日回访率", ret["returned"], ret["eligible"], ratio(prev_ret["returned"], prev_ret["eligible"]),
              targets.get("return7"), "1006–1104"),
        score("样本复购买家比例", lab["positive_pairs"], lab["labeled_pairs"], None,
              targets.get("repeat"), "全训练标签"),
    ]
    bench = benchmark(wh, {
        "detail": f"SELECT merchant_key,uniqExact(user_key) AS uv FROM {db}.dwd_behavior GROUP BY merchant_key ORDER BY merchant_key",
        "summary": f"SELECT merchant_key,uniqExact(user_key) AS uv FROM {db}.dws_merchant_user_day GROUP BY merchant_key ORDER BY merchant_key",
    })
    out = dict(merchant=merchant, snapshot=db, scorecard=items, return_7d_checks=checks,
               item_quality=quality, all_merchant_active_benchmark=bench)
    (ROOT / "outputs/full_validation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
