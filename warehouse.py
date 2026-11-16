"""Load the selected Tianchi CSVs and publish a checked ClickHouse snapshot."""
import argparse
import csv
import hashlib
import json
import os
import time
from collections import Counter
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "天猫电商用户行为与复购数据" / "data_format1" / "data_format1"
SOURCES = {
    "behavior": "user_log_format1.csv",
    "user": "user_info_format1.csv",
    "label": "train_format1.csv",
}
START, END = "0511", "1111"
VERSION = "v1"
BATCH_SIZE = 50_000
CONTROL = "tmall_control.runs"


def local_env():
    path = ROOT / ".env"
    values = {}
    if path.exists():
        values.update(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    values.update({k: v for k, v in os.environ.items() if k.startswith("CLICKHOUSE_")})
    return values


class Warehouse:
    def __init__(self, user="warehouse", secret_key="CLICKHOUSE_PASSWORD"):
        cfg = local_env()
        self.url = cfg.get("CLICKHOUSE_URL", "http://127.0.0.1:18123").rstrip("/")
        self.headers = {"X-ClickHouse-User": user, "X-ClickHouse-Key": cfg[secret_key]}
        self.user = user
        self.http = build_opener(ProxyHandler({}))

    def query(self, sql, data=None):
        args = {"max_threads": "3", "max_memory_usage": str(2_600_000_000),
                "max_bytes_before_external_group_by": str(256_000_000),
                "max_bytes_before_external_sort": str(256_000_000)}
        if self.user == "bi_reader":
            args = {}
        if data is None:
            data = sql.encode("utf-8")
        else:
            args["query"] = sql
        req = Request(self.url + "/?" + urlencode(args), data=data, headers=self.headers)
        try:
            with self.http.open(req, timeout=1200) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8", errors="replace")) from exc

    def rows(self, sql):
        return [json.loads(line) for line in self.query(sql + " FORMAT JSONEachRow").splitlines()]

    def scalar(self, sql):
        return self.query(sql).strip()


def data_files():
    return {name: SOURCE_DIR / filename for name, filename in SOURCES.items()}


def source_identity():
    manifest = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8-sig"))
    indexed = {x["path"]: x for x in manifest}
    selected = {}
    for name, path in data_files().items():
        entry = indexed[path.relative_to(ROOT).as_posix()]
        with path.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        if digest != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError(f"源文件与清单不一致: {path.name}")
        selected[name] = digest
    return selected


def iter_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        expected = {
            SOURCES["behavior"]: ["user_id","item_id","cat_id","seller_id","brand_id","time_stamp","action_type"],
            SOURCES["user"]: ["user_id","age_range","gender"],
            SOURCES["label"]: ["user_id","merchant_id","label"],
        }[path.name]
        if header != expected:
            raise ValueError(f"源表列名不匹配: {path.name}")
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ValueError(f"列数不匹配: {path.name} 第 {row_number} 行")
            yield row_number, row


def load(wh, db, kind, path, sample=False, selected_users=None):
    count = 0
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    for line_no, row in iter_rows(path):
        if sample and kind == "behavior":
            if row[5] not in ("0511", "0512"):
                continue
            selected_users.add(row[0])
        if sample and kind == "user" and row[0] not in selected_users:
            continue
        writer.writerow([line_no, *row])
        count += 1
        if count % BATCH_SIZE == 0:
            wh.query(f"INSERT INTO {db}.ods_{kind} FORMAT CSV", buffer.getvalue().encode())
            buffer.seek(0)
            buffer.truncate(0)
            if count % 5_000_000 == 0:
                print(f"{kind} 已导入 {count:,} 行", flush=True)
    if buffer.tell():
        wh.query(f"INSERT INTO {db}.ods_{kind} FORMAT CSV", buffer.getvalue().encode())
    print(f"ODS {kind}: {count:,} 行", flush=True)
    return count


def controls(wh):
    wh.query("CREATE DATABASE IF NOT EXISTS tmall_control")
    wh.query(f"""CREATE TABLE IF NOT EXISTS {CONTROL} (
        batch String, scope String, snapshot String, status String,
        created DateTime64(3) DEFAULT now64(3)
        ) ENGINE=MergeTree ORDER BY (batch,created)""")


def event(wh, batch, scope, db, status):
    wh.query(f"INSERT INTO {CONTROL} (batch,scope,snapshot,status) VALUES "
             f"('{batch}','{scope}','{db}','{status}')")


@contextmanager
def scope_lock(scope):
    path = Path(os.environ.get("TMALL_LOCK_DIR", str(ROOT))) / f".{scope}.lock"
    with path.open("ab") as lock:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def validate(wh, db, expected):
    counts = {table: int(wh.scalar(f"SELECT count() FROM {db}.{table}")) for table in
              ("ods_behavior","dwd_behavior","dwd_behavior_quarantine","ods_user","ods_label",
               "dwd_repeat_label","dws_merchant_user_day","ads_merchant_period","ads_merchant_return_7d")}
    assert counts["ods_behavior"] == expected["behavior"]
    assert counts["dwd_behavior"] + counts["dwd_behavior_quarantine"] == counts["ods_behavior"]
    assert counts["ods_user"] == expected["user"]
    assert counts["ods_label"] == counts["dwd_repeat_label"] == expected["label"]
    for table, keys in [("ods_user", "user_id"), ("dwd_repeat_label", "tuple(user_key,merchant_key)")]:
        assert wh.scalar(f"SELECT count()-uniqExact({keys}) FROM {db}.{table}") == "0"
    assert wh.scalar(f"""SELECT count() FROM {db}.dwd_behavior b
        LEFT ANTI JOIN {db}.dim_user u ON b.user_key = u.user_key""") == "0"
    assert int(wh.scalar(f"SELECT sum(event_count) FROM {db}.dws_merchant_user_day")) == counts["dwd_behavior"]
    assert int(wh.scalar(f"SELECT sum(event_count) FROM {db}.dws_category_brand_day")) == counts["dwd_behavior"]
    return counts


def evidence(wh, db, scope, batch, counts, elapsed, reused):
    top = wh.rows(f"""SELECT * FROM {db}.ads_merchant_period
        ORDER BY active_users DESC,merchant_key LIMIT 5""")
    rep = wh.rows(f"""SELECT sum(labeled_pairs) AS labeled_pairs,sum(positive_pairs) AS positive_pairs
        FROM {db}.ads_repeat_sample""")[0]
    trace = wh.rows(f"""SELECT source_row,merchant_key,user_key,item_key,mmdd,action_key
        FROM {db}.dwd_behavior WHERE merchant_key={top[0]['merchant_key']}
        ORDER BY day_seq,source_row LIMIT 1""")[0]
    trace["ods"] = wh.rows(f"SELECT * FROM {db}.ods_behavior WHERE source_row={trace['source_row']}")[0]
    trace["dws"] = wh.rows(f"""SELECT * FROM {db}.dws_merchant_user_day
        WHERE merchant_key={trace['merchant_key']} AND user_key={trace['user_key']}
        AND day_seq=(SELECT day_seq FROM {db}.dim_day WHERE mmdd='{trace['mmdd']}')""")[0]
    return {"scope":scope, "batch":batch, "snapshot":db,
            "clickhouse_version":wh.scalar("SELECT version()"), "counts":counts,
            "top_merchants":top, "repeat_sample":rep, "trace":trace,
            "seconds":round(elapsed, 2), "reused":reused}


def run(wh, args):
    began = time.perf_counter()
    hashes = source_identity()
    batch = hashlib.sha256(json.dumps([hashes,args.scope,VERSION,args.purpose], sort_keys=True).encode()).hexdigest()[:20]
    controls(wh)
    previous = wh.scalar(f"SELECT snapshot FROM {CONTROL} WHERE batch='{batch}' AND status='published' ORDER BY created DESC LIMIT 1")
    if previous and not args.fail_after_ods:
        expected = {"behavior":40748 if args.scope=="sample" else 54925330, "label":260864,
                    "user":int(wh.scalar(f"SELECT count() FROM {previous}.ods_user"))}
        counts = validate(wh, previous, expected)
        return evidence(wh,previous,args.scope,batch,counts,time.perf_counter()-began,True)
    db = f"tmall_{args.scope}_{batch}_{uuid4().hex[:8]}"
    event(wh,batch,args.scope,db,"running")
    try:
        wh.query(f"CREATE DATABASE {db}")
        statements = (ROOT / "sql/warehouse.sql").read_text(encoding="utf-8-sig").replace("{db}", db).split(";")
        for stmt in statements[:3]:
            wh.query(stmt)
        paths = data_files()
        users = set()
        sample = args.scope == "sample"
        expected = {k:load(wh,db,k,v,sample,users) for k,v in paths.items()}
        assert expected["behavior"] == (40748 if sample else 54925330)
        assert expected["label"] == 260864
        if not sample:
            assert expected["user"] == 424170
        if args.fail_after_ods:
            raise RuntimeError("演示故障：ODS 已写入，批次尚未发布")
        for stmt in statements[3:]:
            if stmt.strip():
                wh.query(stmt)
        if not sample:
            ret_sql = (ROOT / "sql/return_7d.sql").read_text(encoding="utf-8-sig")
            for bucket in range(16):
                wh.query(ret_sql.format(db=db,buckets=16,bucket=bucket,last_day=185))
        counts = validate(wh,db,expected)
        result = evidence(wh,db,args.scope,batch,counts,time.perf_counter()-began,False)
        expected_quarantine = 0 if sample else 46
        assert counts["dwd_behavior_quarantine"] == expected_quarantine
        assert result["repeat_sample"] == {"labeled_pairs":260864,"positive_pairs":15952}
        event(wh,batch,args.scope,db,"published")
        return result
    except Exception:
        event(wh,batch,args.scope,db,"failed")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["sample","full"], default="sample")
    parser.add_argument("--purpose", choices=["normal","recovery"], default="normal")
    parser.add_argument("--fail-after-ods", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.fail_after_ods and (args.scope != "sample" or args.purpose != "recovery"):
        parser.error("故障注入仅用于样本恢复演示")
    with scope_lock(args.scope):
        result = run(Warehouse(),args)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__ == "__main__":
    main()

