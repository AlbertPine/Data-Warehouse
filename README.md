# 电商商家与用户运营数仓

这是一个用天猫历史样本做**商家运营复盘**的离线数据仓库。看板提供商家活跃趋势、类目与品牌行为、用户回访和样本复购。用商家和日期查询应用层日报，可以查看当天的活跃及购买人数。维护者可以用同一组条件回查行为明细，并用源行号定位原始记录。

项目使用三张真实 CSV（用户行为、用户画像、复购训练标签）。全量原始行为为 **54,925,330 条**。例如，商家 `3828` 在 `0511` 有 **272 条行为、184 位活跃用户和 70 位购买用户**。行为日期采用源数据的月日码，复购比例适用于有效训练标签样本。[阿里云天池官方数据页](https://tianchi.aliyun.com/dataset/42)

## 系统怎么工作

```text
三张 CSV ── Python 检查和分批导入 ──► ClickHouse ODS
                                        ↓ SQL 校验并统一业务键
                                       DWD 行为和标签明细
                                        ↓ SQL 汇总
                                       DWS 公共汇总
                                        ↓ SQL 应用视图
                                       ADS ── 经 tmall_ads 发布 ──► Superset 交互看板
```

Airflow 可以按需启动同一个 Python 加工入口。Superset 用只读账号查询 ClickHouse，用 PostgreSQL 保存看板配置。DBeaver 可用于查表、核对结果和追溯明细。

## 仓库结构

| 位置 | 用途 |
|---|---|
| [warehouse.py](warehouse.py) | 加工主入口。读取和校验 CSV、调用 SQL、登记批次，支持 `sample` 和 `full`。 |
| [sql/](sql/) | ClickHouse 建表、维度、分层汇总和 7 日回访 SQL。 |
| [scripts/](scripts/) | 初始化本机凭据、核对样本、发布 BI 视图、验证和回查。 |
| [airflow/](airflow/) | `tmall_warehouse` DAG，调用同一个加工入口。 |
| [superset/](superset/) | Superset 镜像及配置，启动时创建三页六图。 |
| [compose.yaml](compose.yaml)、[compose.bi.yaml](compose.bi.yaml)、[compose.airflow.yaml](compose.airflow.yaml) | 分别定义 ClickHouse、看板及元数据库、Airflow。 |
| [source_manifest.json](source_manifest.json)、[docs/](docs/README.md) | 源文件路径和 SHA-256 清单，以及需求、指标、设计和操作文档。 |

## Windows 本地部署

在仓库根目录的 PowerShell 中运行下列命令。准备 **Python 3.11+**、**Docker Desktop（Linux 容器）** 和 **Docker Compose v2**。宿主机的 Python 脚本使用标准库，首次部署需要联网拉取镜像和构建 Superset。

### 1. 准备源数据和本机配置

从[天池数据页](https://tianchi.aliyun.com/dataset/42)下载并解压 `data_format1`。按下列路径放入三份原始 CSV，解压时原样保留文件内容。脚本会核对文件大小和 SHA-256。

```text
Data-Warehouse/
└── 天猫电商用户行为与复购数据/
    └── data_format1/
        └── data_format1/
            ├── user_log_format1.csv
            ├── user_info_format1.csv
            └── train_format1.csv
```

```powershell
python --version
docker compose version
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\init_env.ps1
```

初始化脚本会生成本机 `.env`。已有配置时会沿用原文件。原始 CSV、凭据和运行产物由 Git 忽略。

### 2. 先跑两天样本

```powershell
docker compose up -d --wait clickhouse
python -X utf8 warehouse.py --scope sample --output outputs/sample.json
python -X utf8 scripts/check_sample.py
```

样本扫描完整行为 CSV，选取 `0511` 和 `0512` 的 **40,748 条行为**。独立核对会验证前五位商家的活跃及购买人数。再次执行同一批次时，输出的 `reused: true` 表示复用了已通过校验的快照。

### 3. 加工全量并启动看板

依次运行。全量导入完成后，`publish_bi.py` 将全量快照的六个应用视图发布到 `tmall_ads`，Superset 启动时据此加载看板。

```powershell
python -X utf8 warehouse.py --scope full --output outputs/full.json
python -X utf8 scripts/publish_bi.py
docker compose -f compose.yaml -f compose.bi.yaml -f compose.airflow.yaml up -d --wait --build
python -X utf8 scripts/validate_bi.py
python -X utf8 scripts/drilldown.py --merchant 3828 --mmdd 0511
```

| 服务 | 入口 |
|---|---|
| **Superset** | [商家运营总览](http://127.0.0.1:18088/superset/dashboard/tmall-merchants/)。用户名 `admin`，本机密码见 `.env` 的 `SUPERSET_ADMIN_PASSWORD`。 |
| **ClickHouse / DBeaver** | HTTP 主机 `127.0.0.1`、端口 `18123`。查看应用层可用 `bi_reader`，密码取 `CLICKHOUSE_BI_PASSWORD`。 |
| **Airflow** | [本机 Airflow](http://127.0.0.1:18080)。容器启动时生成登录凭据，查看方法见[部署与运维手册](docs/部署与运维手册.md)。 |

初次体验可在 Airflow 里打开 `tmall_warehouse`。先取消 DAG 的暂停状态，再保持默认 `scope=sample` 触发。查到 `reused: true` 即可确认已发布样本能被再次识别。后续生成新的全量快照时，运行 `python -X utf8 scripts/publish_bi.py` 更新看板指向。

项目容器的宿主机端口固定为 `18123`、`18088` 和 `18080`。同机运行已有演示项目时，请先停止占用这些端口的对应容器，再启动本仓库。

## 文档

- [看板操作手册](docs/看板操作手册.md)介绍如何读六张图、应用筛选并回查明细。
- [部署与运维手册](docs/部署与运维手册.md)说明 Airflow、发布权限、失败恢复和故障排查。
- [文档目录](docs/README.md)包含需求分析、数据与指标字典和数仓设计。
