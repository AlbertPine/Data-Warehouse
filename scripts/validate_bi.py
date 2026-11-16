"""Exercise the six saved Superset charts through the local HTTP API."""
import json
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warehouse import ROOT, local_env

BASE = "http://127.0.0.1:18088"
client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
headers = {"Content-Type": "application/json", "Referer": BASE + "/"}


def get(path, payload=None):
    req = Request(BASE + path, None if payload is None else json.dumps(payload).encode(), headers=headers)
    try:
        with client.open(req, timeout=150) as result:
            return json.load(result)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {path}: " + exc.read().decode("utf-8")[:1000]) from exc


def main():
    token = get("/api/v1/security/login", {"username":"admin", "password":local_env()["SUPERSET_ADMIN_PASSWORD"],
                                           "provider":"db"})["access_token"]
    headers["Authorization"] = "Bearer " + token
    headers["X-CSRFToken"] = get("/api/v1/security/csrf_token/")["result"]
    dashboards = get("/api/v1/dashboard/")["result"]
    dashboard_proof = []
    for dash in dashboards:
        row = get(f"/api/v1/dashboard/{dash['id']}")["result"]
        filters = json.loads(row["json_metadata"])["native_filter_configuration"]
        dashboard_proof.append(dict(title=row["dashboard_title"], slug=row["slug"], filters=len(filters)))
    assert len(dashboard_proof) == 3
    proof = []
    for chart in get("/api/v1/chart/")["result"]:
        row = get(f"/api/v1/chart/{chart['id']}")["result"]
        params = json.loads(row["params"])
        dataset = int(params["datasource"].split("__")[0])
        columns = [params["x_axis"]] + params.get("groupby", []) if "x_axis" in params else []
        metrics = params.get("metrics") or [params["metric"]]
        filters = [{"col":f["subject"], "op":"IN", "val":[f["comparator"]]} for f in params.get("adhoc_filters", [])]
        query = dict(columns=columns, metrics=metrics, filters=filters, row_limit=params["row_limit"])
        if columns:
            order = [[columns[0], True]] if params["x_axis_sort"] == "name" else [[metrics[0], False]]
            query["orderby"] = order
        request = dict(datasource={"id":dataset,"type":"table"}, queries=[query],
                       force=True, result_format="json",result_type="full")
        data = get("/api/v1/chart/data", request)["result"][0]
        assert data["data"], row["slice_name"]
        proof.append(dict(title=row["slice_name"], rows=len(data["data"]), first_row=data["data"][0]))
        if row["slice_name"] == "按日查看商家活跃":
            query["filters"].append({"col":"merchant_key","op":"IN","val":[3828]})
            filtered = get("/api/v1/chart/data", request)["result"][0]["data"]
            assert filtered and filtered != data["data"]
            proof[-1]["merchant_3828_filtered_rows"] = len(filtered)
            query["filters"].append({"col":"mmdd","op":"IN","val":["0511"]})
            selected = get("/api/v1/chart/data", request)["result"][0]["data"]
            assert selected == [{"mmdd":"0511",metrics[0]["label"]:184}], selected
            proof[-1]["merchant_3828_mmdd_0511"] = selected[0]
    assert len(proof) == 6
    result = dict(dashboards=dashboard_proof, charts=proof)
    (ROOT / "outputs/bi_validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
