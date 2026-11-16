"""Publish stable ADS names and scoped read privileges after validating the full snapshot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warehouse import Warehouse, local_env

VIEWS = ("merchant_period","merchant_day","category_brand_day",
         "user_age_day","repeat_sample","merchant_return_7d")

wh = Warehouse()
snapshot = wh.scalar("""SELECT snapshot FROM tmall_control.runs
    WHERE scope='full' AND status='published' ORDER BY created DESC LIMIT 1""")
if not snapshot or not snapshot.replace("_","").isalnum():
    raise RuntimeError("尚无完整且已验证的全量快照")

wh.query("CREATE DATABASE IF NOT EXISTS tmall_ads")
pw = local_env()["CLICKHOUSE_BI_PASSWORD"]
if not pw.isalnum():
    raise RuntimeError("请在 .env 使用随机字母数字密码")
wh.query(f"CREATE USER IF NOT EXISTS bi_reader IDENTIFIED WITH sha256_password BY '{pw}' SETTINGS readonly=1")
wh.query(f"ALTER USER bi_reader IDENTIFIED WITH sha256_password BY '{pw}'")
for name in VIEWS:
    wh.query(f"""CREATE OR REPLACE VIEW tmall_ads.{name}
        DEFINER = CURRENT_USER SQL SECURITY DEFINER AS SELECT * FROM {snapshot}.ads_{name}""")
wh.query("GRANT SELECT ON tmall_ads.* TO bi_reader")
bi = Warehouse("bi_reader","CLICKHOUSE_BI_PASSWORD")
for name in VIEWS:
    print(name, bi.scalar(f"SELECT count() FROM tmall_ads.{name}"))
try:
    bi.scalar(f"SELECT count() FROM {snapshot}.ods_behavior")
except RuntimeError as exc:
    if "ACCESS_DENIED" not in str(exc) and "privilege" not in str(exc):
        raise
else:
    raise AssertionError("BI 用户可以读取原始层")
print("只读账户校验通过")
