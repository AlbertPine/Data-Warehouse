"""Seed the local course dashboard using Superset's mapped objects."""
import json
import os
import sys

from superset.app import create_app

app = create_app()
from superset.extensions import db, security_manager
from superset.models.core import Database
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.connectors.sqla.models import SqlaTable

with app.app_context():
    admin = security_manager.find_user(username="admin")
    if not admin:
        admin = security_manager.add_user("admin", "Course", "Admin", "admin@example.invalid",
                                          security_manager.find_role("Admin"),
                                          password=os.environ["SUPERSET_ADMIN_PASSWORD"])
    name = "天猫电商用户行为与复购数据 · 应用层"
    legacy_name = "天猫 42 已发布应用层"
    database = db.session.query(Database).filter(Database.database_name.in_((name, legacy_name))).one_or_none()
    if sys.argv[1:] == ["--rename-database-only"]:
        if database is None:
            raise RuntimeError("未找到项目原有的 Superset 数据连接")
        database.database_name = name
        db.session.commit()
        print(f"Superset database id={database.id} name={database.database_name}", flush=True)
        sys.exit(0)
    if database is None:
        database = Database(database_name=name)
        db.session.add(database)
    else:
        database.database_name = name
    secret = os.environ["CLICKHOUSE_BI_PASSWORD"]
    database.set_sqlalchemy_uri(f"clickhousedb://bi_reader:{secret}@clickhouse:8123/tmall_ads")
    db.session.commit()

    charts = [
        ("商家活跃用户与购买用户", "merchant_period", "echarts_timeseries_bar", "merchant_key",
         [("活跃用户数", "max(active_users)"), ("购买用户数", "max(purchase_users)")], ["merchant_key"]),
        ("按日查看商家活跃", "merchant_day", "echarts_timeseries_bar", "mmdd",
         [("商家日活跃人次", "sum(active_users)")], ["merchant_key","mmdd"]),
        ("类目与品牌行为分布", "category_brand_day", "echarts_timeseries_bar", "category_key",
         [("行为数", "sum(event_count)"), ("购买行为数", "sum(purchase_event_count)")],
         ["category_key","brand_key","mmdd"]),
        ("用户分群活跃", "user_age_day", "echarts_timeseries_bar", "age_group",
         [("所选分组日活跃人数合计", "sum(active_users)")], ["mmdd","age_group","gender_group"]),
        ("标签样本复购比例", "repeat_sample", "big_number_total", None,
         [("复购样本比例", "sum(positive_pairs)/nullIf(sum(labeled_pairs),0)")], ["merchant_key"]),
        ("商家 7 日回访率", "merchant_return_7d", "echarts_timeseries_bar", "mmdd",
         [("回访比例", "sum(returned_users)/nullIf(sum(eligible_users),0)")], ["merchant_key","mmdd"]),
    ]

    def metric(label, expression):
        return {"expressionType":"SQL","sqlExpression":expression,"label":label,
                "optionName":expression,"hasCustomLabel":True}

    def upsert_chart(spec):
        title, table, viz, axis, measures, _ = spec
        dataset = db.session.query(SqlaTable).filter_by(database_id=database.id,table_name=table,schema="tmall_ads").one_or_none()
        if dataset is None:
            dataset = SqlaTable(table_name=table,schema="tmall_ads",database=database,owners=[admin])
            db.session.add(dataset)
            db.session.flush()
        dataset.fetch_metadata()
        chart = db.session.query(Slice).filter_by(slice_name=title).one_or_none()
        if chart is None:
            chart = Slice(slice_name=title,owners=[admin])
            db.session.add(chart)
        chart.datasource_id, chart.datasource_type, chart.viz_type = dataset.id, "table", viz
        params = {"viz_type":viz,"datasource":f"{dataset.id}__table","row_limit":15}
        if axis:
            params.update(x_axis=axis, metrics=[metric(*m) for m in measures],
                          x_axis_sort="value", x_axis_sort_asc=False,
                          time_range="No filter", adhoc_filters=[])
        else:
            params.update(metric=metric(*measures[0]),y_axis_format=".2%",header_font_size=0.4)
        if table in ("merchant_day","merchant_return_7d","user_age_day"):
            params["row_limit"] = 500
            params["x_axis_sort"] = "name"
            params["x_axis_sort_asc"] = True
        if table == "merchant_return_7d":
            params["y_axis_format"] = ".1%"
        chart.params = json.dumps(params)
        db.session.commit()
        return chart, dataset

    saved = [upsert_chart(spec) for spec in charts]
    for title,slug,indices in [
        ("商家运营总览","tmall-merchants",[0,1]),
        ("类目与品牌对比","tmall-category",[2]),
        ("用户分群与样本复购","tmall-users",[3,4,5]),
    ]:
        dash = db.session.query(Dashboard).filter_by(slug=slug).one_or_none()
        if dash is None:
            dash = Dashboard(dashboard_title=title,slug=slug,published=True,owners=[admin])
            db.session.add(dash)
        dash.slices = [saved[i][0] for i in indices]
        pos = {"DASHBOARD_VERSION_KEY":"v2",
               "ROOT_ID":{"id":"ROOT_ID","type":"ROOT","children":["GRID_ID"]},
               "GRID_ID":{"id":"GRID_ID","type":"GRID","parents":["ROOT_ID"],"children":[]},
               "HEADER_ID":{"id":"HEADER_ID","type":"HEADER","meta":{"text":title}}}
        filters = []
        for i in indices:
            chart, dataset = saved[i]
            row = f"ROW-{i}"
            key = f"CHART-{chart.id}"
            pos["GRID_ID"]["children"].append(row)
            pos[row] = {"id":row,"type":"ROW","parents":["ROOT_ID","GRID_ID"],"children":[key],
                        "meta":{"background":"BACKGROUND_TRANSPARENT"}}
            pos[key] = {"id":key,"type":"CHART","parents":["ROOT_ID","GRID_ID",row],"children":[],
                        "meta":{"chartId":chart.id,"height":44,"width":12,"sliceName":chart.slice_name}}
            for col in charts[i][5]:
                default = {"extraFormData":{},"filterState":{},"ownState":{}}
                if i == 3 and col == "mmdd":
                    default = {"extraFormData":{"filters":[{"col":"mmdd","op":"IN","val":["1111"]}]},
                               "filterState":{"value":["1111"],"label":"1111"},"ownState":{}}
                filters.append({"id":f"NATIVE_FILTER-{i}-{col}","name":f"{chart.slice_name} · {col}",
                    "filterType":"filter_select","type":"NATIVE_FILTER","targets":[{"datasetId":dataset.id,"column":{"name":col}}],
                    "defaultDataMask":default,
                    "controlValues":{"multiSelect":True,"enableEmptyFilter":False,"searchAllOptions":True},
                    "cascadeParentIds":[],"scope":{"rootPath":["ROOT_ID"],
                    "excluded":[saved[j][0].id for j in indices if j!=i]}})
        dash.position_json = json.dumps(pos,ensure_ascii=False)
        dash.json_metadata = json.dumps({"native_filter_configuration":filters})
        db.session.commit()
    print("Superset: 3 dashboards, 6 datasets, 6 charts")

