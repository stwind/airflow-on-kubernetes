# Bare Minimal Airflow On Kubernetes

Airflow and Kubernetes are perfect match, but they are also complicated beasts. There are many attempts (see [References](#references))  to provide partial or complete deployment solution with custom helm charts. But instead of directly installing the them, usually one just look around for useful snippets and ideas to build their own solution.

In the repo, instead of providing another full feature helm chart or terraform module, I try to use just command line to put a minimal Airflow on Kubernetes, so anyone interested could just copy and paste to reproduce the same results, maybe as a starting point or trouble shooting tool for their own solution.

##Prerequisites

* Local
  * Docker For Mac: `19.03.5`
  * Kubernetes: `v1.15.5`
  * Helm: `v3.0.1`
* EKS
* GKE
* AKS

## Preparation

Airflow does [not allow SQLite to be used with the kubernetes executor](https://github.com/apache/airflow/blob/6fffa5b0d7840727a96dc1765a0166656bc7ea52/airflow/configuration.py#L170), so you need to have a MySQL or PostgreSQL server. For this demostration, we use MySQL.

### Build the docker image

```shell
$ docker build -t my/airflow -<<'EOF'
FROM python:3.7.6-slim

ARG AIRFLOW_USER_HOME=/etc/airflow
ARG AIRFLOW_USER="airflow"
ARG AIRFLOW_UID="1000"
ARG AIRFLOW_GID="100"
ENV AIRFLOW_HOME=$AIRFLOW_USER_HOME

RUN mkdir $AIRFLOW_USER_HOME && \
  useradd -ms /bin/bash -u "$AIRFLOW_UID" airflow && \
  chown $AIRFLOW_USER:$AIRFLOW_GID $AIRFLOW_USER_HOME && \
  buildDeps='freetds-dev libkrb5-dev libsasl2-dev libssl-dev libffi-dev libpq-dev' \
  apt-get update && \
  apt-get install -yqq --no-install-recommends $buildDeps build-essential default-libmysqlclient-dev && \
  pip install --no-cache-dir 'apache-airflow[crypto,kubernetes,mysql]' && \
  apt-get purge --auto-remove -yqq $buildDeps && \
  apt-get autoremove -yqq --purge && \
  rm -rf /var/lib/apt/lists/*

USER $AIRFLOW_UID

WORKDIR $AIRFLOW_USER_HOME
EOF
```

## Environments

### Local

#### MySQL

In case you don't have one yet, you can install with helm.

Save the following the content as `mysql/values.yaml`

```yaml
---
# put your more serious password here
mysqlRootPassword: root
mysqlUser: airflow
mysqlPassword: airflow
mysqlDatabase: airflow

configurationFiles:
  mysql.cnf: |-
    [mysqld]
    # https://airflow.apache.org/docs/stable/faq.html#how-to-fix-exception-global-variable-explicit-defaults-for-timestamp-needs-to-be-on-1
    explicit_defaults_for_timestamp=1
```

Install the [MySQL chart](https://github.com/helm/charts/tree/master/stable/mysql)

```shell
$ helm install -f mysql/values.yaml mysql stable/mysql
```

Make sure the `explicit_defaults_for_timestamp` is `ON`

```sh
$ kubectl exec -ti $(kubectl get po -l app=mysql,release=mysql -o jsonpath="{.items[0].metadata.name}") -- mysql -u root -p -e "SHOW VARIABLES LIKE 'explicit_defaults_for_timestamp'"
Enter password:
+---------------------------------+-------+
| Variable_name                   | Value |
+---------------------------------+-------+
| explicit_defaults_for_timestamp | ON    |
+---------------------------------+-------+
```

#### Initialize Database

```sh
$ kubectl run airflow-initdb \
		--restart=Never -ti --rm --image-pull-policy=IfNotPresent --generator=run-pod/v1 \
		--image=my/airflow \
		--env AIRFLOW__CORE__LOAD_EXAMPLES=False \
		--env AIRFLOW__CORE__SQL_ALCHEMY_CONN=mysql://airflow:airflow@mysql.default/airflow \
		--command -- airflow initdb
```

#### Start Airflow

```sh
$ kubectl run airflow -ti --rm --restart=Never --image=pngu/alpine --overrides='
{
  "spec": {
    "containers":[{
      "name": "webserver",
      "image": "pngu/airflow",
      "imagePullPolicy":"IfNotPresent",
      "command": ["airflow","webserver"],
      "stdin": true,
      "tty": true,
      "env": [
        {"name":"AIRFLOW__CORE__LOAD_EXAMPLES","value":"False"},
        {"name":"AIRFLOW__CORE__SQL_ALCHEMY_CONN","value":"mysql://airflow:airflow@mysql.default/airflow"}, 
        {"name":"AIRFLOW__CORE__EXECUTOR","value":"KubernetesExecutor"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_REPOSITORY","value":"pngu/airflow"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_TAG","value":"latest"},
        {"name":"AIRFLOW__KUBERNETES__DAGS_VOLUME_HOST","value":"'$PWD/dags'"}
      ],
      "volumeMounts": [{"mountPath": "/etc/airflow/dags","name": "store"}]
    },{
      "name": "scheduler",
      "image": "pngu/airflow",
      "imagePullPolicy":"IfNotPresent",
      "command": ["airflow","scheduler"],
      "stdin": true,
      "tty": true,
      "env": [
        {"name":"AIRFLOW__CORE__LOAD_EXAMPLES","value":"False"},
        {"name":"AIRFLOW__CORE__SQL_ALCHEMY_CONN","value":"mysql://airflow:airflow@mysql.default/airflow"}, 
        {"name":"AIRFLOW__CORE__EXECUTOR","value":"KubernetesExecutor"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_REPOSITORY","value":"pngu/airflow"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_TAG","value":"latest"},
        {"name":"AIRFLOW__KUBERNETES__DAGS_VOLUME_HOST","value":"'$PWD/dags'"},
        {"name":"AIRFLOW__KUBERNETES__KUBE_CLIENT_REQUEST_ARGS","value":""}
      ],
      "volumeMounts": [{"mountPath": "/etc/airflow/dags","name": "store"}]
    }],
    "volumes": [{"name":"store","hostPath":{"path":"'$PWD/dags'","type":"Directory"}}]
  }
}'
```

This will show the logs of webserver, you can also show the scheduler logs by running the following comamnd (on another shell):

```sh
$ kubectl logs -f airflow -c scheduler
```

Also, open another shell and run this command to see how pods come and go

```sh
$ kubectl get po -w
```

#### Testing

Open another shell, list the dags by running the following 

```sh
$ kubectl exec -ti airflow -c webserver airflow list_dags
```

Unpause and trigger the dag

```sh
$ kubectl exec -ti airflow -c webserver airflow unpause test
$ kubectl exec -ti airflow -c webserver airflow trigger_dag test
```

#### Cleanup

Delete the pod and MySQL

```sh
$ kubectl delete po airflow
$ helm delete mysql
```

## References

* [Bitnami Apache Airflow Multi-Tier now available in Azure Marketplace | Blog | Microsoft Azure](https://azure.microsoft.com/en-us/blog/bitnami-apache-airflow-multi-tier-now-available-in-azure-marketplace/)
* [tekn0ir/airflow-chart: Helm chart for deploying Apache Airflow in kubernetes](https://github.com/tekn0ir/airflow-chart)
* [puckel/docker-airflow: Docker Apache Airflow](https://github.com/puckel/docker-airflow)
* [GoogleCloudPlatform/airflow-operator: Kubernetes custom controller and CRDs to managing Airflow](https://github.com/GoogleCloudPlatform/airflow-operator)
* [villasv/aws-airflow-stack: Turbine: the bare metals behind a complete Airflow setup](https://github.com/villasv/aws-airflow-stack)
* [mumoshu/kube-airflow: A docker image and kubernetes config files to run Airflow on Kubernetes](https://github.com/mumoshu/kube-airflow)
* [rolanddb/airflow-on-kubernetes: A guide to running Airflow on Kubernetes](https://github.com/rolanddb/airflow-on-kubernetes)
* [EamonKeane/airflow-GKE-k8sExecutor-helm: Quickly get a kubernetes executor airflow environment provisioned on GKE. Azure Kubernetes Service instructions included also as are instructions for docker-for-mac.](https://github.com/EamonKeane/airflow-GKE-k8sExecutor-helm)