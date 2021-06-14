from typing import Any, Callable, Dict, Optional
from datetime import datetime, timedelta, timezone

from airflow.models import DAG, Variable, DagRun, DagModel, TaskInstance, BaseOperator, XCom, XCOM_RETURN_KEY
from airflow.kubernetes.secret import Secret
from airflow.configuration import conf
from airflow.utils.weight_rule import WeightRule

from __lib import alert_failed_to_slack, SuperKubernetesPodOperator, SuperExternalTaskSensor, \
    SlackWebhookOperator, CrossTenantDependencySensor

SENSOR_DEFAULT_POKE_INTERVAL_IN_SECS = int(Variable.get("sensor_poke_interval_in_secs", default_var=15 * 60))
SENSOR_DEFAULT_TIMEOUT_IN_SECS = int(Variable.get("sensor_timeout_in_secs", default_var=15 * 60 * 60))
DAG_RETRIES = int(Variable.get("dag_retries", default_var=3))
DAG_RETRY_DELAY = int(Variable.get("dag_retry_delay_in_secs", default_var=5 * 60))

default_args = {
    "owner": "mee@mee",
    "depends_on_past": False,
    "retries": DAG_RETRIES,
    "retry_delay": timedelta(seconds=DAG_RETRY_DELAY),
    "retry_exponential_backoff": False,
    "priority_weight": 2000,
    "start_date": datetime.strptime("2000-11-11T00:00:00", "%Y-%m-%dT%H:%M:%S"),
    "end_date": datetime.strptime("2020-11-11T00:00:00","%Y-%m-%dT%H:%M:%S"),
    "on_failure_callback": alert_failed_to_slack,
    "weight_rule": WeightRule.ABSOLUTE
}

dag = DAG(
    dag_id="foo",
    default_args=default_args,
    schedule_interval="* * * * *",
    catchup = True
)

transformation_secret = Secret(
    "volume",
    "/opt/optimus/secrets",
    "optimus-task-bq",
    "auth.json"
)
transformation_bq = SuperKubernetesPodOperator(
    image_pull_policy="Always",
    namespace = conf.get('kubernetes', 'namespace', fallback="default"),
    image = "example.io/namespace/image:latest",
    cmds=[],
    name="bq",
    task_id="bq",
    get_logs=True,
    dag=dag,
    in_cluster=True,
    is_delete_operator_pod=True,
    do_xcom_push=False,
    secrets=[transformation_secret],
    env_vars={
        "JOB_NAME":'foo', "OPTIMUS_HOSTNAME":'http://airflow.example.io',
        "JOB_LABELS":'orchestrator=optimus',
        "JOB_DIR":'/data', "PROJECT":'foo-project',
        "INSTANCE_TYPE":'task', "INSTANCE_NAME":'bq',
        "SCHEDULED_AT":'{{ next_execution_date }}',
    },
    reattach_on_restart=True,
)

# hooks loop start

hook_transporter_secret = Secret(
    "volume",
    "/tmp",
    "optimus-hook-transporter",
    "auth.json"
)

hook_transporter = SuperKubernetesPodOperator(
    image_pull_policy="Always",
    namespace = conf.get('kubernetes', 'namespace', fallback="default"),
    image = "example.io/namespace/hook-image:latest",
    cmds=[],
    name="hook_transporter",
    task_id="hook_transporter",
    get_logs=True,
    dag=dag,
    in_cluster=True,
    is_delete_operator_pod=True,
    do_xcom_push=False,
    secrets=[hook_transporter_secret],
    env_vars={
        "JOB_NAME":'foo', "OPTIMUS_HOSTNAME":'http://airflow.example.io',
        "JOB_LABELS":'orchestrator=optimus',
        "JOB_DIR":'/data', "PROJECT":'foo-project',
        "INSTANCE_TYPE":'hook', "INSTANCE_NAME":'transporter',
        "SCHEDULED_AT":'{{ next_execution_date }}',
        # rest of the env vars are pulled from the container by making a GRPC call to optimus
   },
   reattach_on_restart=True,
)


hook_predator = SuperKubernetesPodOperator(
    image_pull_policy="Always",
    namespace = conf.get('kubernetes', 'namespace', fallback="default"),
    image = "example.io/namespace/predator-image:latest",
    cmds=[],
    name="hook_predator",
    task_id="hook_predator",
    get_logs=True,
    dag=dag,
    in_cluster=True,
    is_delete_operator_pod=True,
    do_xcom_push=False,
    secrets=[],
    env_vars={
        "JOB_NAME":'foo', "OPTIMUS_HOSTNAME":'http://airflow.example.io',
        "JOB_LABELS":'orchestrator=optimus',
        "JOB_DIR":'/data', "PROJECT":'foo-project',
        "INSTANCE_TYPE":'hook', "INSTANCE_NAME":'predator',
        "SCHEDULED_AT":'{{ next_execution_date }}',
        # rest of the env vars are pulled from the container by making a GRPC call to optimus
   },
   reattach_on_restart=True,
)
# hooks loop ends


# create upstream sensors

wait_foo__dash__intra__dash__dep__dash__job = SuperExternalTaskSensor(
    external_dag_id = "foo-intra-dep-job",
    window_size = "1h0m0s",
    window_offset = "0s",
    window_truncate_to = "d",
    optimus_hostname = "http://airflow.example.io",
    task_id = "wait_foo-intra-dep-job-bq",
    poke_interval = SENSOR_DEFAULT_POKE_INTERVAL_IN_SECS,
    timeout = SENSOR_DEFAULT_TIMEOUT_IN_SECS,
    dag=dag
)
wait_foo__dash__inter__dash__dep__dash__job = CrossTenantDependencySensor(
    optimus_hostname="http://airflow.example.io",
    optimus_project="foo-external-project",
    optimus_job="foo-inter-dep-job",
    poke_interval=SENSOR_DEFAULT_POKE_INTERVAL_IN_SECS,
    timeout=SENSOR_DEFAULT_TIMEOUT_IN_SECS,
    task_id="wait_foo-inter-dep-job-bq",
    dag=dag
)

# arrange inter task dependencies
####################################

# upstream sensors -> base transformation task
wait_foo__dash__intra__dash__dep__dash__job >> transformation_bq
wait_foo__dash__inter__dash__dep__dash__job >> transformation_bq

# set inter-dependencies between task and hooks
hook_transporter >> transformation_bq
transformation_bq >> hook_predator

# set inter-dependencies between hooks and hooks
hook_transporter >> hook_predator