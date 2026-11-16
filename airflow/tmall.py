from datetime import datetime, timezone

from airflow.sdk import DAG, Param
from airflow.providers.standard.operators.bash import BashOperator

with DAG(
    dag_id="tmall_warehouse",
    start_date=datetime(2026, 11, 14, tzinfo=timezone.utc),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params={
        "scope": Param("sample", enum=["sample", "full"]),
        "purpose": Param("normal", enum=["normal", "recovery"]),
        "fail_after_ods": Param(False, type="boolean"),
    },
) as dag:
    BashOperator(
        task_id="load_validate_publish",
        bash_command="""set -eu
python /project/warehouse.py --scope "$TMALL_SCOPE" --purpose "$TMALL_PURPOSE" $TMALL_FAULT
""",
        env={
            "TMALL_SCOPE": "{{ params.scope }}",
            "TMALL_PURPOSE": "{{ params.purpose }}",
            "TMALL_FAULT": "{{ '--fail-after-ods' if params.fail_after_ods else '' }}",
        },
        append_env=True,
    )
