# Code generated by optimus {{.Version}}. DO NOT EDIT.

from typing import Any, Callable, Dict, Optional
from datetime import datetime, timedelta, timezone

from airflow.models import DAG, Variable, DagRun, DagModel, TaskInstance, BaseOperator, XCom, XCOM_RETURN_KEY
from airflow.configuration import conf
from airflow.operators.python_operator import PythonOperator
from airflow.utils.weight_rule import WeightRule
from kubernetes.client import models as k8s


from __lib import optimus_sla_miss_notify, SuperKubernetesPodOperator, \
    SuperExternalTaskSensor, ExternalHttpSensor

from __lib import JOB_START_EVENT_NAME, \
    JOB_END_EVENT_NAME, \
    log_start_event, \
    log_success_event, \
    log_retry_event, \
    log_failure_event, \
    EVENT_NAMES, \
    log_job_end, log_job_start

SENSOR_DEFAULT_POKE_INTERVAL_IN_SECS = int(Variable.get("sensor_poke_interval_in_secs", default_var=15 * 60))
SENSOR_DEFAULT_TIMEOUT_IN_SECS = int(Variable.get("sensor_timeout_in_secs", default_var=15 * 60 * 60))
DAG_RETRIES = int(Variable.get("dag_retries", default_var=3))
DAG_RETRY_DELAY = int(Variable.get("dag_retry_delay_in_secs", default_var=5 * 60))
DAGRUN_TIMEOUT_IN_SECS = int(Variable.get("dagrun_timeout_in_secs", default_var=3 * 24 * 60 * 60))

default_args = {
    "params": {
        "project_name": {{.Namespace.ProjectSpec.Name | quote}},
        "namespace": {{.Namespace.Name | quote}},
        "job_name": {{.Job.Name | quote}},
        "optimus_hostname": {{.Hostname | quote}}
    },
    {{- if ne .Metadata.Airflow.Pool "" }}
    "pool": "{{ .Metadata.Airflow.Pool }}",
    {{- end }}
    {{- if ne .Metadata.Airflow.Queue "" }}
    "queue": "{{ .Metadata.Airflow.Queue }}",
    {{- end }}
    "owner": {{.Job.Owner | quote}},
    "depends_on_past": False,
    "retries": {{ if gt .Job.Behavior.Retry.Count 0 -}} {{.Job.Behavior.Retry.Count}} {{- else -}} DAG_RETRIES {{- end}},
    "retry_delay": {{ if gt .Job.Behavior.Retry.Delay.Nanoseconds 0 -}} timedelta(seconds={{.Job.Behavior.Retry.Delay.Seconds}}) {{- else -}} timedelta(seconds=DAG_RETRY_DELAY) {{- end}},
    "retry_exponential_backoff": {{if .Job.Behavior.Retry.ExponentialBackoff -}}True{{- else -}}False{{- end -}},
    "priority_weight": {{.Job.Task.Priority}},
    "start_date": datetime.strptime({{ .Job.Schedule.StartDate.Format "2006-01-02T15:04:05" | quote }}, "%Y-%m-%dT%H:%M:%S"),
    {{if .Job.Schedule.EndDate -}}"end_date": datetime.strptime({{ .Job.Schedule.EndDate.Format "2006-01-02T15:04:05" | quote}},"%Y-%m-%dT%H:%M:%S"),{{- else -}}{{- end}}
    "on_failure_callback": log_failure_event,
    "on_retry_callback": log_retry_event,
    "on_success_callback": log_success_event,
    "weight_rule": WeightRule.ABSOLUTE
}

dag = DAG(
    dag_id={{.Job.Name | quote}},
    default_args=default_args,
    schedule_interval={{ if eq .Job.Schedule.Interval "" }}None{{- else -}} {{ .Job.Schedule.Interval | quote}}{{end}},
    sla_miss_callback=optimus_sla_miss_notify,
    catchup={{ if .Job.Behavior.CatchUp -}}True{{- else -}}False{{- end }},
    dagrun_timeout=timedelta(seconds=DAGRUN_TIMEOUT_IN_SECS),
    tags = [
            {{- range $key, $value := $.Job.Labels}}
            "{{ $value }}",
            {{- end}}
           ]
)

publish_job_start_event = PythonOperator(
        task_id = JOB_START_EVENT_NAME,
        python_callable = log_job_start,
        provide_context=True,
        dag=dag
    )

publish_job_end_event = PythonOperator(
        task_id = JOB_END_EVENT_NAME,
        python_callable = log_job_end,
        provide_context=True,
        trigger_rule= 'all_success',
        dag=dag
    )

{{$baseTaskSchema := .Job.Task.Unit.Info -}}
{{- $setCPURequest := not (empty .Metadata.Resource.Request.CPU) -}}
{{- $setMemoryRequest := not (empty .Metadata.Resource.Request.Memory) -}}
{{- $setCPULimit := not (empty .Metadata.Resource.Limit.CPU) -}}
{{- $setMemoryLimit := not (empty .Metadata.Resource.Limit.Memory) -}}
{{- $setResourceConfig := or $setCPURequest $setMemoryRequest $setCPULimit $setMemoryLimit }}

{{- if $setResourceConfig }}
resources = k8s.V1ResourceRequirements (
    {{- if or $setCPURequest $setMemoryRequest }}
    requests = {
        {{- if $setMemoryRequest }}
        'memory': '{{.Metadata.Resource.Request.Memory}}',
        {{- end }}
        {{- if $setCPURequest }}
        'cpu': '{{.Metadata.Resource.Request.CPU}}',
        {{- end }}
    },
    {{- end }}
    {{- if or $setCPULimit $setMemoryLimit }}
    limits = {
        {{- if $setMemoryLimit }}
        'memory': '{{.Metadata.Resource.Limit.Memory}}',
        {{- end }}
        {{- if $setCPULimit }}
        'cpu': '{{.Metadata.Resource.Limit.CPU}}',
        {{- end }}
    },
    {{- end }}
)
{{- end }}
JOB_DIR = "/data"
IMAGE_PULL_POLICY="IfNotPresent"
INIT_CONTAINER_IMAGE="odpf/optimus:{{.Version}}"
INIT_CONTAINER_ENTRYPOINT = "/opt/entrypoint_init_container.sh"

volume = k8s.V1Volume(
    name='asset-volume',
    empty_dir=k8s.V1EmptyDirVolumeSource()
)
asset_volume_mounts = [
    k8s.V1VolumeMount(mount_path=JOB_DIR, name='asset-volume', sub_path=None, read_only=False)
]
executor_env_vars = [
    k8s.V1EnvVar(name="JOB_LABELS",value='{{.Job.GetLabelsAsString}}'),
    k8s.V1EnvVar(name="JOB_DIR",value=JOB_DIR),
]

init_env_vars = [
    k8s.V1EnvVar(name="JOB_DIR",value=JOB_DIR),
    k8s.V1EnvVar(name="JOB_NAME",value='{{$.Job.Name}}'),
    k8s.V1EnvVar(name="OPTIMUS_HOST",value='{{$.Hostname}}'),
    k8s.V1EnvVar(name="PROJECT",value='{{$.Namespace.ProjectSpec.Name}}'),
    k8s.V1EnvVar(name="SCHEDULED_AT",value='{{ "{{ next_execution_date }}" }}'),
]

init_container = k8s.V1Container(
    name="init-container",
    image=INIT_CONTAINER_IMAGE,
    image_pull_policy=IMAGE_PULL_POLICY,
    env=init_env_vars + [
        k8s.V1EnvVar(name="INSTANCE_TYPE",value='{{$.InstanceTypeTask}}'),
        k8s.V1EnvVar(name="INSTANCE_NAME",value='{{$baseTaskSchema.Name}}'),
    ],
    security_context=k8s.V1PodSecurityContext(run_as_user=0),
    volume_mounts=asset_volume_mounts,
    command=["/bin/sh", INIT_CONTAINER_ENTRYPOINT],
)

transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}} = SuperKubernetesPodOperator(
    optimus_hostname="{{$.Hostname}}",
    optimus_projectname="{{$.Namespace.ProjectSpec.Name}}",
    optimus_namespacename="{{$.Namespace.Name}}",
    optimus_jobname="{{.Job.Name}}",
    optimus_jobtype="{{$.InstanceTypeTask}}",
    image_pull_policy=IMAGE_PULL_POLICY,
    namespace = conf.get('kubernetes', 'namespace', fallback="default"),
    image = {{ $baseTaskSchema.Image | quote}},
    cmds=[],
    name="{{ $baseTaskSchema.Name | replace "_" "-" }}",
    task_id={{$baseTaskSchema.Name | quote}},
    get_logs=True,
    dag=dag,
    depends_on_past={{ if .Job.Behavior.DependsOnPast }}True{{- else -}}False{{- end -}},
    in_cluster=True,
    is_delete_operator_pod=True,
    do_xcom_push=False,
    env_vars=executor_env_vars,
{{- if gt .SLAMissDurationInSec 0 }}
    sla=timedelta(seconds={{ .SLAMissDurationInSec }}),
{{- end }}
{{- if $setResourceConfig }}
    resources = resources,
{{- end }}
    reattach_on_restart=True,
    volume_mounts=asset_volume_mounts,
    volumes=[volume],
    init_containers=[init_container],
)

# hooks loop start
{{ range $_, $t := .Job.Hooks }}
{{ $hookSchema := $t.Unit.Info -}}

init_container_{{$hookSchema.Name | replace "-" "__dash__"}} = k8s.V1Container(
    name="init-container",
    image=INIT_CONTAINER_IMAGE,
    image_pull_policy=IMAGE_PULL_POLICY,
    env= init_env_vars + [
        k8s.V1EnvVar(name="INSTANCE_TYPE",value='{{$.InstanceTypeHook}}'),
        k8s.V1EnvVar(name="INSTANCE_NAME",value='{{$hookSchema.Name}}'),
    ],
    security_context=k8s.V1PodSecurityContext(run_as_user=0),
    volume_mounts=asset_volume_mounts,
    command=["/bin/sh", INIT_CONTAINER_ENTRYPOINT],
)

hook_{{$hookSchema.Name | replace "-" "__dash__"}} = SuperKubernetesPodOperator(
    optimus_hostname="{{$.Hostname}}",
    optimus_projectname="{{$.Namespace.ProjectSpec.Name}}",
    optimus_namespacename="{{$.Namespace.Name}}",
    optimus_jobname="{{$.Job.Name}}",
    optimus_jobtype="{{$.InstanceTypeHook}}",
    image_pull_policy=IMAGE_PULL_POLICY,
    namespace = conf.get('kubernetes', 'namespace', fallback="default"),
    image = "{{ $hookSchema.Image }}",
    cmds=[],
    name="hook_{{ $hookSchema.Name | replace "_" "-"}}",
    task_id="hook_{{ $hookSchema.Name }}",
    get_logs=True,
    dag=dag,
    in_cluster=True,
    is_delete_operator_pod=True,
    do_xcom_push=False,
    env_vars=executor_env_vars,
{{- if eq $hookSchema.HookType $.HookTypeFail }}
    trigger_rule="one_failed",
{{- end }}
{{- if $setResourceConfig }}
    resources = resources,
{{- end }}
    reattach_on_restart=True,
    volume_mounts=asset_volume_mounts,
    volumes=[volume],
    init_containers=[init_container_{{$hookSchema.Name | replace "-" "__dash__"}}],
)
{{- end }}
# hooks loop ends


# create upstream sensors
{{ $baseWindow := $.Job.Task.Window }}
{{- range $_, $dependency := $.Job.Dependencies}}
{{- $dependencySchema := $dependency.Job.Task.Unit.Info }}
wait_{{$dependency.Job.Name | replace "-" "__dash__" | replace "." "__dot__"}} = SuperExternalTaskSensor(
    optimus_hostname="{{$.Hostname}}",
    upstream_optimus_hostname="{{$.Hostname}}",
    upstream_optimus_project="{{$dependency.Project.Name}}",
    upstream_optimus_namespace="{{$dependency.Job.NamespaceSpec.Name}}",
    upstream_optimus_job="{{$dependency.Job.Name}}",
    window_size="{{ $baseWindow.GetSize }}",
    window_version=int("{{ $baseWindow.GetVersion }}"),
    poke_interval=SENSOR_DEFAULT_POKE_INTERVAL_IN_SECS,
    timeout=SENSOR_DEFAULT_TIMEOUT_IN_SECS,
    task_id="wait_{{$dependency.Job.Name | trunc 200}}-{{$dependencySchema.Name}}",
    dag=dag
)
{{- end}}

{{- range $_, $dependency := $.Job.ExternalDependencies.OptimusDependencies}}
{{ $identity := print $dependency.Name "-" $dependency.ProjectName "-" $dependency.JobName }}
wait_{{ $identity | replace "-" "__dash__" | replace "." "__dot__"}} = SuperExternalTaskSensor(
    optimus_hostname="{{$.Hostname}}",
    upstream_optimus_hostname="{{$dependency.Host}}",
    upstream_optimus_project="{{$dependency.ProjectName}}",
    upstream_optimus_namespace="{{$dependency.NamespaceName}}",
    upstream_optimus_job="{{$dependency.JobName}}",
    window_size="{{ $baseWindow.GetSize }}",
    window_version=int("{{ $baseWindow.GetVersion }}"),
    poke_interval=SENSOR_DEFAULT_POKE_INTERVAL_IN_SECS,
    timeout=SENSOR_DEFAULT_TIMEOUT_IN_SECS,
    task_id="wait_{{$dependency.JobName | trunc 200}}-{{$dependency.TaskName}}",
    dag=dag
)
{{- end}}

{{- range $_, $httpDependency := $.Job.ExternalDependencies.HTTPDependencies}}  # merged from http and optimus?
headers_dict_{{$httpDependency.Name}} = { {{- range $k, $v := $httpDependency.Headers}} '{{$k}}': '{{$v}}', {{- end}} }
request_params_dict_{{$httpDependency.Name}} = { {{- range $key, $value := $httpDependency.RequestParams}} '{{$key}}': '{{$value}}', {{- end}} }

wait_{{$httpDependency.Name}} = ExternalHttpSensor(
    endpoint='{{$httpDependency.URL}}',
    headers=headers_dict_{{$httpDependency.Name}},
    request_params=request_params_dict_{{$httpDependency.Name}},
    poke_interval=SENSOR_DEFAULT_POKE_INTERVAL_IN_SECS,
    timeout=SENSOR_DEFAULT_TIMEOUT_IN_SECS,
    task_id='wait_{{$httpDependency.Name| trunc 200}}',
    dag=dag
)
{{- end}}

# arrange inter task dependencies
####################################

# upstream sensors -> base transformation task
{{- range $i, $t := $.Job.Dependencies }}
publish_job_start_event >> wait_{{ $t.Job.Name | replace "-" "__dash__" | replace "." "__dot__" }} >> transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}}
{{- end}}
{{- range $_, $t := $.Job.ExternalDependencies.HTTPDependencies }}
publish_job_start_event >>  wait_{{ $t.Name }} >> transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}}
{{- end}}
{{- range $_, $dependency := $.Job.ExternalDependencies.OptimusDependencies}}
{{ $identity := print $dependency.Name "-" $dependency.ProjectName "-" $dependency.JobName }}
publish_job_start_event >> wait_{{ $identity | replace "-" "__dash__" | replace "." "__dot__" }} >> transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}}
{{- end}}
{{if and (not $.Job.Dependencies) (not $.Job.ExternalDependencies.HTTPDependencies) (not $.Job.ExternalDependencies.OptimusDependencies)}}
# if no sensor and dependency is configured
publish_job_start_event >> transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}}
{{end}}
# post completion hook
transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}} >> publish_job_end_event

# set inter-dependencies between task and hooks
{{- range $_, $task := .Job.Hooks }}
{{- $hookSchema := $task.Unit.Info }}
{{- if eq $hookSchema.HookType $.HookTypePre }}
publish_job_start_event >> hook_{{$hookSchema.Name | replace "-" "__dash__"}} >> transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}}
{{- end -}}
{{- if eq $hookSchema.HookType $.HookTypePost }}
transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}} >> hook_{{$hookSchema.Name | replace "-" "__dash__"}} >> publish_job_end_event
{{- end -}}
{{- if eq $hookSchema.HookType $.HookTypeFail }}
transformation_{{$baseTaskSchema.Name | replace "-" "__dash__" | replace "." "__dot__"}} >> hook_{{$hookSchema.Name | replace "-" "__dash__"}} >> publish_job_end_event
{{- end -}}
{{- end }}

# set inter-dependencies between hooks and hooks
{{- range $_, $t := .Job.Hooks }}
{{- $hookSchema := $t.Unit.Info }}
{{- range $_, $depend := $t.DependsOn }}
{{- $dependHookSchema := $depend.Unit.Info }}
hook_{{$dependHookSchema.Name | replace "-" "__dash__"}} >> hook_{{$hookSchema.Name | replace "-" "__dash__"}} >> publish_job_end_event
{{- end }}
{{- end }}

# arrange failure hook after post hooks
{{- range $_, $task := .Job.Hooks -}}
{{- $hookSchema := $task.Unit.Info }}

{{- if eq $hookSchema.HookType $.HookTypePost }}

hook_{{$hookSchema.Name | replace "-" "__dash__"}} >> [
{{- range $_, $ftask := $.Job.Hooks }}
{{- $fhookSchema := $ftask.Unit.Info }}
{{- if eq $fhookSchema.HookType $.HookTypeFail }} hook_{{$fhookSchema.Name | replace "-" "__dash__"}}, {{- end -}}
{{- end -}}
] >> publish_job_end_event

{{- end -}}

{{- end -}}
