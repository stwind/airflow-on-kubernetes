# Bare Minimal Airflow On Kubernetes

Airflow and Kubernetes are perfect match, but they are complicated beasts to each their own. There are many [attempts](#references) to provide partial or complete deployment solution with custom helm charts. But usually one just look around for useful snippets and ideas to build their own solution instead of directly installing them.

In the repo, instead of providing another full feature helm chart or terraform module, I try to use just command line to setup a minimal Airflow on Kubernetes. Anyone interested could hopefully just copy and paste to reproduce the same results, maybe as a starting point or trouble shooting tool for their own solution.

## <a name="toc"></a>Contents

* [Prerequisites](#prerequisites)
* [Preparation](#preparation)
* [Environments](#environments)
	* [Local](#local)
		* [MySQL](#local-mysql)
		* [Initialize Database](#local-init-db)
		* [Start Airflow](#local-start)
		* [Testing](#local-test)
		* [Cleanup](#local-cleanup)
  * [EKS](#eks)
	  * [IAM Setup](#eks-iam)
	  * [ECR Setup](#eks-ecr)
	  * [Create Cluster](#eks-cluster)
	  * [Create RDS](#eks-rds)
	  * [Connect VPCs](#eks-vpc)
	  * [Testing Airflow](#eks-test)
	  * [Cleanup](#eks-cleanup)
  * [AKS](#aks)
	  * [ACR setup](#aks-acr)
	  * [Cluster Setup](#aks-cluster)
	  * [MySQL Setup](#aks-mysql)
	  * [Prepare Volume](#aks-vol)
	  * [Testing Airflow](#aks-test)
	  * [Cleanup](#aks-cleanup)

## Prerequisites

* common
  * [jq](https://stedolan.github.io/jq/): `1.6`
* Local
  * Docker For Mac: `19.03.5`
  * Kubernetes: `v1.15.5`
  * Helm: `v3.0.1`
* EKS
  * [awscli](https://aws.amazon.com/cli/): `1.16.130`
  * [eksctl](https://eksctl.io/): `0.11.1`
* AKS
  * [azure-cli](https://docs.microsoft.com/en-us/cli/azure/): `2.0.78`

## Preparation

Airflow does [not allow SQLite to be used with the kubernetes executor](https://github.com/apache/airflow/blob/6fffa5b0d7840727a96dc1765a0166656bc7ea52/airflow/configuration.py#L170), so you need to have a MySQL or PostgreSQL server. For this demostration, we use MySQL.

### Build the docker image

```shell
$ docker build -t my/airflow -<<'EOF'
FROM python:3.7.6-slim

ARG AIRFLOW_USER_HOME=/var/lib/airflow
ARG AIRFLOW_USER="airflow"
ARG AIRFLOW_UID="1000"
ARG AIRFLOW_GID="100"
ENV AIRFLOW_HOME=$AIRFLOW_USER_HOME

RUN mkdir $AIRFLOW_USER_HOME && \
  useradd -ms /bin/bash -u $AIRFLOW_UID airflow && \
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

### <a name="local"></a>Local

#### <a name="local-mysql"></a>MySQL [▲](#toc) 

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

Because `helm init` no longer exists in Helm 3, we need to add stable repo manually

```shell
$ helm repo add stable https://kubernetes-charts.storage.googleapis.com/
$ helm install -f mysql/values.yaml mysql stable/mysql
```

Make sure the `explicit_defaults_for_timestamp` is `ON`

```sh
$ kubectl exec -ti $(kubectl get po -l app=mysql,release=mysql -o jsonpath="{.items[0].metadata.name}") -- mysql -u root -p -e "SHOW VARIABLES LIKE 'explicit_defaults_for_timestamp'"
Enter password: root
+---------------------------------+-------+
| Variable_name                   | Value |
+---------------------------------+-------+
| explicit_defaults_for_timestamp | ON    |
+---------------------------------+-------+
```

#### <a name="local-init-db"></a>Initialize Database [▲](#toc)

```sh
$ kubectl run airflow-initdb \
    --restart=Never -ti --rm --image-pull-policy=IfNotPresent --generator=run-pod/v1 \
    --image=my/airflow \
    --env AIRFLOW__CORE__LOAD_EXAMPLES=False \
    --env AIRFLOW__CORE__SQL_ALCHEMY_CONN=mysql://airflow:airflow@mysql.default/airflow \
    --command -- airflow initdb
```

#### <a name="local-start"></a>Start Airflow [▲](#toc)

```sh
$ kubectl run airflow -ti --rm --restart=Never --image=my/airflow --overrides='
{
  "spec": {
    "containers":[{
      "name": "webserver",
      "image": "my/airflow",
      "imagePullPolicy":"IfNotPresent",
      "command": ["airflow","webserver"],
      "stdin": true,
      "tty": true,
      "env": [
        {"name":"AIRFLOW__CORE__LOAD_EXAMPLES","value":"False"},
        {"name":"AIRFLOW__CORE__SQL_ALCHEMY_CONN","value":"mysql://airflow:airflow@mysql.default/airflow"}, 
        {"name":"AIRFLOW__CORE__EXECUTOR","value":"KubernetesExecutor"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_REPOSITORY","value":"my/airflow"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_TAG","value":"latest"},
        {"name":"AIRFLOW__KUBERNETES__DAGS_VOLUME_HOST","value":"'$PWD/dags'"}
      ],
      "volumeMounts": [{"mountPath": "/var/lib/airflow/dags","name": "store"}]
    },{
      "name": "scheduler",
      "image": "my/airflow",
      "imagePullPolicy":"IfNotPresent",
      "command": ["airflow","scheduler"],
      "stdin": true,
      "tty": true,
      "env": [
        {"name":"AIRFLOW__CORE__LOAD_EXAMPLES","value":"False"},
        {"name":"AIRFLOW__CORE__SQL_ALCHEMY_CONN","value":"mysql://airflow:airflow@mysql.default/airflow"}, 
        {"name":"AIRFLOW__CORE__EXECUTOR","value":"KubernetesExecutor"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_REPOSITORY","value":"my/airflow"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_TAG","value":"latest"},
        {"name":"AIRFLOW__KUBERNETES__DAGS_VOLUME_HOST","value":"'$PWD/dags'"},
        {"name":"AIRFLOW__KUBERNETES__KUBE_CLIENT_REQUEST_ARGS","value":""}
      ],
      "volumeMounts": [{"mountPath": "/var/lib/airflow/dags","name": "store"}]
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

Or if you want to see the UI

```sh
$ kubectl port-forward airflow 8080
```

#### <a name="local-test"></a>Testing [▲](#toc)

Open another shell, list the dags by running the following 

```sh
$ kubectl exec -ti airflow -c webserver airflow list_dags
-------------------------------------------------------------------
DAGS
-------------------------------------------------------------------
foobar
```

Unpause and trigger the dag

```sh
$ kubectl exec -ti airflow -c webserver airflow unpause foobar
$ kubectl exec -ti airflow -c webserver airflow trigger_dag foobar
$ kubectl exec -ti airflow -c webserver airflow list_dag_runs foobar
```

#### <a name="local-cleanup"></a>Cleanup [▲](#toc)

Delete the pod and MySQL

```sh
$ kubectl delete po airflow
$ helm delete mysql
```

### <a name="eks"></a>EKS

Some environment variables we will use later

```sh
$ export AWS_REGION=$(aws configure get region)
$ export AWS_ACCOUNT_ID=$(aws sts get-caller-identity | jq -r '.Account')
```

#### <a name="eks-iam"></a>IAM Setup [▲](#toc) 

Attach the following managed IAM policies

|Policy|Arn|
|---|---|
|[`AmazonEC2ContainerRegistryFullAccess`]((https://docs.aws.amazon.com/AmazonECR/latest/userguide/ecr_managed_policies.html#AmazonEC2ContainerRegistryFullAccess))|`arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess`|
|`AWSCloudFormationFullAccess`|`arn:aws:iam::aws:policy/AWSCloudFormationFullAccess `|
|`AmazonEC2FullAccess`|`arn:aws:iam::aws:policy/AmazonEC2FullAccess`|
|`AmazonECS_FullAccess`|`arn:aws:iam::aws:policy/AmazonECS_FullAccess`|
|[`AmazonRDSFullAccess`](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.access-control.html#USER_PerfInsights.access-control.managed-policy)|`arn:aws:iam::aws:policy/AmazonRDSFullAccess`|

and create a custom policy called `AmazonEKSFullAccess` and attach to your user or group

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "eks:*",
      "Resource": "*"
    }
  ]
}
```

#### <a name="eks-ecr"></a>ECR Setup [▲](#toc)

First we have to setup a ECR repository to hold the image, you can create it in the web console or using the cli.

Here we create a repository called `airflow`

```sh
$ aws ecr create-repository --repository-name airflow
```

login to the registry with the output of

```sh
$ aws ecr get-login --registry-ids $(aws ecr describe-repositories --repository-names airflow | jq '.repositories[0].registryId' -r)
```

push the image (replace `account_id` and `region` with your own)

```sh
$ docker tag my/airflow ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/airflow
$ docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/airflow
```

when done, your image should be in the repository

```sh
$ aws ecr describe-images --repository-name airflow
```

#### <a name="eks-cluster"></a>Create EKS cluster [▲](#toc)

Create a minimal EKS cluster called `airflow-test`

```sh
$ eksctl create cluster --name airflow-test --version 1.14 --nodes 1 --ssh-access
```

After a few minutes for cluster, you should have a new cluster in your kubectl contexts, run the following commands to make sure

```sh
$ kubectl config get-contexts
$ kubectl config current-context
$ kubectl cluster-info
```

get more information about the cluster

```sh
$ eksctl get cluster -n airflow-test
```

#### <a name="eks-rds"></a>Create RDS [▲](#toc)

Now we are going to create an RDS to hold the data.

The cluster we just created is in its own VPC, but you probably don't want the RDS in the same VPC with the cluster. We are going to create another VPC and use [VPC Peering](https://docs.aws.amazon.com/vpc/latest/peering/what-is-vpc-peering.html) to connect them. Most of the following techniques are borrowed from this [great article](https://dev.to/bensooraj/accessing-amazon-rds-from-aws-eks-2pc3). 

To summarize, an [RDS in VPC](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html#USER_VPC.Subnets) requires **an VPC with 2 subnets in different availability zones**.

First we create the VPC and save the resulting VPC id to `RDS_VPC_ID` and enable DNS hostnames support

```sh
$ export RDS_VPC_CIDR_BLOCK=10.0.0.0/24
$ aws ec2 create-vpc --cidr-block ${RDS_VPC_CIDR_BLOCK} | jq -r '{VpcId:.Vpc.VpcId,CidrBlock:.Vpc.CidrBlock}'
{
  "VpcId": "vpc-0c92ff62833b93381",
  "CidrBlock": "10.0.0.0/24"
}
$ export RDS_VPC_ID=vpc-0c92ff62833b93381
$ aws ec2 modify-vpc-attribute --vpc-id ${RDS_VPC_ID} --enable-dns-hostnames '{"Value":true}'
$ aws ec2 create-tags --resources ${RDS_VPC_ID} --tags Key=Name,Value=airflow/VPC
```

Next let's check what availability zones are in the current region

```sh
$ aws ec2 describe-availability-zones --region $(aws configure get region) | jq -r '.AvailabilityZones[].ZoneName'
ap-northeast-1a
ap-northeast-1c
ap-northeast-1d
```

Create two subnets in two different availability zones

```sh
$ aws ec2 create-subnet --availability-zone "ap-northeast-1a" --vpc-id ${RDS_VPC_ID} --cidr-block "10.0.0.0/25" | jq '{SubnetId:.Subnet.SubnetId,AvailabilityZone:.Subnet.AvailabilityZone,CidrBlock:.Subnet.CidrBlock,VpcId:.Subnet.VpcId}'
{
  "SubnetId": "subnet-0cb408bee95aa7fb3",
  "AvailabilityZone": "ap-northeast-1a",
  "CidrBlock": "10.0.0.0/25",
  "VpcId": "vpc-0c92ff62833b93381"
}
$ aws ec2 create-tags --resources "subnet-0cb408bee95aa7fb3" --tags Key=Name,Value=airflow/Subnet1
$ aws ec2 create-subnet --availability-zone "ap-northeast-1c" --vpc-id ${RDS_VPC_ID} --cidr-block 10.0.0.128/25 | jq -r '{SubnetId:.Subnet.SubnetId,AvailabilityZone:.Subnet.AvailabilityZone,CidrBlock:.Subnet.CidrBlock,VpcId:.Subnet.VpcId}'
{
  "SubnetId": "subnet-04871a023fdf047ec",
  "AvailabilityZone": "ap-northeast-1c",
  "CidrBlock": "10.0.0.128/25",
  "VpcId": "vpc-0c92ff62833b93381"
}
$ aws ec2 create-tags --resources "subnet-04871a023fdf047ec" --tags Key=Name,Value=airflow-rds/Subnet1
```

Associate the subnets with their VPC's default route table

```sh
$ export RDS_ROUTE_TABLE_ID=$(aws ec2 describe-route-tables --filters Name=vpc-id,Values=${RDS_VPC_ID} | jq -r '.RouteTables[0].RouteTableId')
$ aws ec2 associate-route-table --route-table-id ${RDS_ROUTE_TABLE_ID} --subnet-id "subnet-04871a023fdf047ec"
{
    "AssociationId": "rtbassoc-0d4b9e2d85994a074"
}
$ aws ec2 associate-route-table --route-table-id ${RDS_ROUTE_TABLE_ID} --subnet-id "subnet-0cb408bee95aa7fb3"
{
    "AssociationId": "rtbassoc-0ee558d300f3bb605"
}
```

Create a subnet group

```sh
$ aws rds create-db-subnet-group --db-subnet-group-name "airflow" --db-subnet-group-description "Subnet Group For Airflow" --subnet-ids "subnet-04871a023fdf047ec" "subnet-0cb408bee95aa7fb3" | jq -r '{DBSubnetGroupName:.DBSubnetGroup.DBSubnetGroupName,VpcId:.DBSubnetGroup.VpcId,Subnets:.DBSubnetGroup.Subnets[].SubnetIdentifier}'
{
  "DBSubnetGroupName": "airflow",
  "VpcId": "vpc-0c92ff62833b93381",
  "Subnets": "subnet-04871a023fdf047ec"
}
{
  "DBSubnetGroupName": "airflow",
  "VpcId": "vpc-0c92ff62833b93381",
  "Subnets": "subnet-0cb408bee95aa7fb3"
}
```

Create a security group

```sh
$ aws ec2 create-security-group --group-name airflow-rds/SecurityGroup --description "Airflow RDS security group" --vpc-id ${RDS_VPC_ID}
{
    "GroupId": "sg-00ec4f82799271da0"
}
$ export RDS_VPC_SECURITY_GROUP_ID=sg-00ec4f82799271da0
```

Now create the DB instance

```sh
$ aws rds create-db-instance \
  --db-name airflow \
  --db-instance-identifier airflow \
  --allocated-storage 10 \
  --db-instance-class db.t2.micro \
  --engine mysql \
  --engine-version "5.7.16" \
  --master-username airflowmaster \
  --master-user-password airflowmaster \
  --no-publicly-accessible \
  --no-multi-az \
  --no-auto-minor-version-upgrade \
  --vpc-security-group-ids ${RDS_VPC_SECURITY_GROUP_ID} \
  --db-subnet-group-name "airflow" \
  --backup-retention-period 0 \
  --port 3306 | jq -r '{DBInstanceIdentifier:.DBInstance.DBInstanceIdentifier,Engine:.DBInstance.Engine,DBName:.DBInstance.DBName,VpcSecurityGroups:.DBInstance.VpcSecurityGroups,EngineVersion:.DBInstance.EngineVersion,PubliclyAccessible:.DBInstance.PubliclyAccessible}'
{
  "DBInstanceIdentifier": "airflow",
  "Engine": "mysql",
  "DBName": "airflow",
  "VpcSecurityGroups": [
    {
      "VpcSecurityGroupId": "sg-00ec4f82799271da0",
      "Status": "active"
    }
  ],
  "EngineVersion": "5.7.16",
  "PubliclyAccessible": false
}
```

The default parameter group for RDS mysql 5.7 already have `explicit_defaults_for_timestamp` set to `ON`

```sh
$ aws rds describe-engine-default-parameters --db-parameter-group-family mysql5.7 | jq -r '.EngineDefaults.Parameters[] | select(.ParameterName=="explicit_defaults_for_timestamp") | {ParameterName:.ParameterName,ParameterValue:.ParameterValue}'
{
  "ParameterName": "explicit_defaults_for_timestamp",
  "ParameterValue": "1"
}
```

#### <a name="eks-vpc"></a>Connect VPCs [▲](#toc)

We are now going to connect the two VPCs of the eks cluster and the RDS. 

Let's first figure out the VPC ID of the eks cluster

```sh
$ export EKS_VPC_ID=$(aws eks describe-cluster --name airflow-test | jq -r '.cluster.resourcesVpcConfig.vpcId')
```

Initiate a connection from the cluster to rds

```sh
$ aws ec2 create-vpc-peering-connection --vpc-id ${EKS_VPC_ID} --peer-vpc-id ${RDS_VPC_ID} | jq -r '{VpcPeeringConnectionId:.VpcPeeringConnection.VpcPeeringConnectionId}'
{
  "VpcPeeringConnectionId": "pcx-008a8b86c35e07ff4"
}
$ export VPC_PEERING_CONNECTION_ID=pcx-008a8b86c35e07ff4
```

Accept the connection

```sh
$ aws ec2 accept-vpc-peering-connection --vpc-peering-connection-id ${VPC_PEERING_CONNECTION_ID}
```

Give it a name `airflow/VpcPeering` and enable [DNS resolution](https://docs.aws.amazon.com/vpc/latest/peering/modify-peering-connections.html#vpc-peering-dns)

```sh
$ aws ec2 create-tags --resources ${VPC_PEERING_CONNECTION_ID} --tags Key=Name,Value=airflow/VpcPeering
$ aws ec2 modify-vpc-peering-connection-options \
    --vpc-peering-connection-id ${VPC_PEERING_CONNECTION_ID} \
    --requester-peering-connection-options '{"AllowDnsResolutionFromRemoteVpc":true}' \
    --accepter-peering-connection-options '{"AllowDnsResolutionFromRemoteVpc":true}'
```

Update the route tables of the cluster and RDS to route mutual traffic

```sh
$ export EKS_ROUTE_TABLE_ID=$(aws ec2 describe-route-tables --filters 'Name="tag:aws:cloudformation:logical-id",Values="PublicRouteTable"' 'Name="tag:alpha.eksctl.io/cluster-name",Values="airflow-test"' | jq -r '.RouteTables[0].RouteTableId')
$ export EKS_VPC_CIDR_BLOCK=$(aws ec2 describe-vpcs --vpc-id $(eksctl get cluster -n airflow-test -o json | jq -rj '.[0].ResourcesVpcConfig.VpcId') | jq -rj '.Vpcs[0].CidrBlock')
$ aws ec2 create-route --route-table-id ${EKS_ROUTE_TABLE_ID} --destination-cidr-block ${RDS_VPC_CIDR_BLOCK} --vpc-peering-connection-id ${VPC_PEERING_CONNECTION_ID}
$ aws ec2 create-route --route-table-id ${RDS_ROUTE_TABLE_ID} --destination-cidr-block ${EKS_VPC_CIDR_BLOCK} --vpc-peering-connection-id ${VPC_PEERING_CONNECTION_ID}
```

Update RDS's security group to allow mysql connection 

```sh
$ aws ec2 authorize-security-group-ingress --group-id ${RDS_VPC_SECURITY_GROUP_ID} --protocol tcp --port 3306 --cidr ${EKS_VPC_CIDR_BLOCK}
```

Let's test the connection. First create an [ExternalName](https://kubernetes.io/docs/concepts/services-networking/service/#externalname) service as an alias of the RDS endpoint

```sh
$ export RDS_ENDPOINT=$(aws rds describe-db-instances --filters Name=db-instance-id,Values=airflow | jq -r '.DBInstances[0].Endpoint.Address')
$ kubectl create service externalname mysql --external-name ${RDS_ENDPOINT}
```

Test the RDS port with `nc` with and alpine container

```sh
$ kubectl run test-mysql --restart=Never -ti --rm \
  --image=alpine --generator=run-pod/v1 --image-pull-policy=IfNotPresent \
  --command -- sh -c 'nc -zv mysql 3306 &> /dev/null && echo "online" || echo "offline"'
online
pod "test-mysql" deleted
```

#### <a name="eks-test"></a>Testing Airflow [▲](#toc)

We are now ready to test airflow. First let's initialize the database

```sh
$ kubectl run airflow-initdb \
    --restart=Never -ti --rm --image-pull-policy=IfNotPresent --generator=run-pod/v1 \
    --image=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/airflow \
    --env AIRFLOW__CORE__LOAD_EXAMPLES=False \
    --env AIRFLOW__CORE__SQL_ALCHEMY_CONN=mysql://airflowmaster:airflowmaster@mysql/airflow \
    --command -- airflow initdb
```

Copy dags onto every nodes

```sh
$ export EKS_AUTOSCALLING_GROUP=$(aws cloudformation describe-stack-resources --stack-name $(eksctl get nodegroup --cluster airflow-test -o json | jq -r '.[0].StackName') | jq -r '.StackResources[] | select(.ResourceType=="AWS::AutoScaling::AutoScalingGroup") | .PhysicalResourceId')
$ declare -a EKS_NODE_INSTANCE_IDS=(`aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names ${EKS_AUTOSCALLING_GROUP} | jq -r '.AutoScalingGroups[0].Instances[].InstanceId'`)
for EKS_NODE_INSTANCE_ID in "${EKS_NODE_INSTANCE_IDS[@]}"
do
    EKS_NODE_HOST=$(aws ec2 describe-instances --instance-ids ${EKS_NODE_INSTANCE_ID} | jq -r '.Reservations[0].Instances[0].PublicDnsName')
    ssh -q -o "UserKnownHostsFile=/dev/null" -o "StrictHostKeyChecking=no" ec2-user@${EKS_NODE_HOST} "sudo rm /opt/dags/*"
    tar -cf - dags/*.py | ssh -q -o "UserKnownHostsFile=/dev/null" -o "StrictHostKeyChecking=no" ec2-user@${EKS_NODE_HOST} "sudo tar -x --no-same-owner -C /opt"
done
```

Create a service account, and give it the necessary permissions

```sh
$ kubectl apply -f - <<'EOF'
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: airflow
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: airflow
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "watch", "list"]
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "watch", "list"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["get", "create"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
---
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: airflow
subjects:
  - kind: ServiceAccount
    name: airflow
roleRef:
  kind: Role
  name: airflow
  apiGroup: rbac.authorization.k8s.io
EOF
```

Now run the scheduler (skipping the webserver this time)

```sh
$ kubectl run airflow -ti --rm --restart=Never \
    --image=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/airflow --overrides='
{
  "spec": {
    "serviceAccountName":"airflow",
    "containers":[{
      "name": "scheduler",
      "image": "'${AWS_ACCOUNT_ID}'.dkr.ecr.'${AWS_REGION}'.amazonaws.com/airflow",
      "imagePullPolicy":"IfNotPresent",
      "command": ["airflow","scheduler"],
      "stdin": true,
      "tty": true,
      "env": [
        {"name":"AIRFLOW__CORE__LOAD_EXAMPLES","value":"False"},
        {"name":"AIRFLOW__CORE__SQL_ALCHEMY_CONN","value":"mysql://airflowmaster:airflowmaster@mysql/airflow"}, 
        {"name":"AIRFLOW__CORE__EXECUTOR","value":"KubernetesExecutor"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_REPOSITORY","value":"'${AWS_ACCOUNT_ID}'.dkr.ecr.'${AWS_REGION}'.amazonaws.com/airflow"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_TAG","value":"latest"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_SERVICE_ACCOUNT_NAME","value":"airflow"},
        {"name":"AIRFLOW__KUBERNETES__KUBE_CLIENT_REQUEST_ARGS","value":""},
        {"name":"AIRFLOW__KUBERNETES__DAGS_VOLUME_HOST","value":"/opt/dags"}
      ],
      "volumeMounts": [{"mountPath": "/var/lib/airflow/dags","name": "dags"}]
    }],
    "volumes": [{"name":"dags","hostPath":{"path":"/opt/dags","type":"Directory"}}]
  }
}'
```

Open another terminal to watch pods status

```sh
$ kubectl get po -w
```

We are now ready to test airflow functionalities

```sh
$ kubectl exec -ti airflow airflow list_dags
$ kubectl exec -ti airflow airflow unpause foobar
$ kubectl exec -ti airflow airflow trigger_dag foobar
$ kubectl exec -ti airflow airflow list_dag_runs foobar
```

the pods life cycle would be something like

```
NAME                                         READY   STATUS    RESTARTS   AGE
foobarfoo-b3bbbef345334d58863d0da4e11faba9   0/1     Pending   0          <invalid>
foobarfoo-b3bbbef345334d58863d0da4e11faba9   0/1     Pending   0          <invalid>
foobarfoo-b3bbbef345334d58863d0da4e11faba9   0/1     ContainerCreating   0          <invalid>
foobarfoo-b3bbbef345334d58863d0da4e11faba9   1/1     Running             0          0s
foobarbar-d5457f56526a4084bb964942a18f95d9   0/1     Pending             0          <invalid>
foobarbar-d5457f56526a4084bb964942a18f95d9   0/1     Pending             0          <invalid>
foobarbar-d5457f56526a4084bb964942a18f95d9   0/1     ContainerCreating   0          <invalid>
foobarbar-d5457f56526a4084bb964942a18f95d9   1/1     Running             0          0s
foobarfoo-b3bbbef345334d58863d0da4e11faba9   0/1     Completed           0          9s
foobarfoo-b3bbbef345334d58863d0da4e11faba9   0/1     Terminating         0          10s
foobarfoo-b3bbbef345334d58863d0da4e11faba9   0/1     Terminating         0          10s
foobarbar-d5457f56526a4084bb964942a18f95d9   0/1     Completed           0          9s
foobarbar-d5457f56526a4084bb964942a18f95d9   0/1     Terminating         0          10s
foobarbar-d5457f56526a4084bb964942a18f95d9   0/1     Terminating         0          10s

```

#### <a name="eks-cleanup"></a>Cleanup [▲](#toc)

Cleanup the VPC routes

```sh
$ aws ec2 delete-route --route-table-id ${RDS_ROUTE_TABLE_ID} --destination-cidr-block ${EKS_VPC_CIDR_BLOCK}
$ aws ec2 delete-route --route-table-id ${EKS_ROUTE_TABLE_ID} --destination-cidr-block ${RDS_VPC_CIDR_BLOCK}
$ aws ec2 delete-vpc-peering-connection --vpc-peering-connection-id ${VPC_PEERING_CONNECTION_ID}
```

Delete the cluster

```sh
$ eksctl delete cluster -n airflow-test
```

Delete the RDS and related VPC resources

```sh
$ aws rds delete-db-instance --db-instance-identifier airflow --skip-final-snapshot
$ aws rds delete-db-subnet-group --db-subnet-group-name airflow
$ aws ec2 delete-subnet --subnet-id subnet-0cb408bee95aa7fb3
$ aws ec2 delete-subnet --subnet-id subnet-04871a023fdf047ec
$ aws ec2 delete-security-group --group-id ${RDS_VPC_SECURITY_GROUP_ID}
$ aws ec2 delete-vpc --vpc-id ${RDS_VPC_ID}
```

Delete images and repository

```sh
$ aws ecr batch-delete-image --repository-name airflow --image-ids $(aws ecr list-images --repository-name airflow | jq -r '.imageIds | reduce .[] as $img (""; . + "imageDigest=\($img.imageDigest) ")')
$ aws ecr delete-repository --repository-name airflow
```

### <a name="aks"></a>AKS

First create an `airflow` resource group

```sh
$ az group create --name airflow
```

#### <a name="aks-acr"></a>ACR Setup [▲](#toc)

Create a `Basic` registry and login

```sh
$ az acr create -g airflow -n airflow --sku Basic
$ az acr login -n airflow
```

Push the image

```sh
$ docker tag my/airflow airflow.azurecr.io/airflow
$ docker push airflow.azurecr.io/airflow
```

Confirm the image is successfully pushed

```sh
$ az acr repository show-tags -n airflow --repository airflow
```

#### <a name="aks-cluster"></a>Cluster Setup [▲](#toc)

Create a cluster with the ACR attached

```sh
$ az aks create -g airflow -n airflow \
  --kubernetes-version 1.15.5 \
  --node-count 1
  --attach-acr airflow
```

Configure local context

```sh
$ az aks get-credentials -g airflow --name airflow
$ kubectl cluster-info
```

Run a simple pod

```sh
$ kubectl run echo -ti --rm --image=alpine --generator=run-pod/v1 --image-pull-policy=IfNotPresent --command -- echo hello
```

#### <a name="aks-mysql"></a>MySQL Setup [▲](#toc)

Create the server and the database

```sh
$ az mysql server create -g airflow -n airflow \
  --admin-user airflowmaster \
  --admin-password airflowMa5ter \
  --sku-name GP_Gen5_2 \
  --version 5.7
$ az mysql db create -g airflow -s airflow -n airflow
```

Enable a `Microsoft.SQL` service endpoint on the cluster subnet

```sh
$ export AKS_NODE_RESOURCE_GROUP=$(az aks show -n airflow -g airflow --query nodeResourceGroup -o tsv)
$ export AKS_VNET_NAME=$(az resource list -g ${AKS_NODE_RESOURCE_GROUP} --query "[?type=='Microsoft.Network/virtualNetworks'] | [0].name" -o tsv)
$ az network vnet subnet update --vnet-name ${AKS_VNET_NAME} -n aks-subnet -g ${AKS_NODE_RESOURCE_GROUP} --service-endpoints Microsoft.SQL
```

Enable MySQL access from the cluster

```sh
$ export AKS_SUBNET_ID=$(az network vnet show -n ${AKS_VNET_NAME} -g ${AKS_NODE_RESOURCE_GROUP} --query 'subnets[0].id' -o tsv)
$ az mysql server vnet-rule create -n aks-mysql -g airflow -s airflow --subnet ${AKS_SUBNET_ID}
```

Check connection and confirm `explicit_defaults_for_timestamp` is `ON`

```sh
$ kubectl run test-mysql -ti --rm --image=alpine --generator=run-pod/v1 --image-pull-policy=IfNotPresent --command -- \
    sh -c "apk -U add mysql-client && mysql -h airflow.mysql.database.azure.com -u airflowmaster -pairflowMa5ter --ssl -e \"SHOW VARIABLES LIKE 'explicit_defaults_for_timestamp'\""
+---------------------------------+-------+
| Variable_name                   | Value |
+---------------------------------+-------+
| explicit_defaults_for_timestamp | ON    |
+---------------------------------+-------+
```

#### <a name="aks-vol"></a>Prepare Volume [▲](#toc)

Create a storage account and use a Secret to hold it

```sh
$ export AKS_STORAGEACCT=$(az storage account create -g airflow -n airflow$RANDOM --sku Standard_LRS --query "name" -o tsv)
$ export AKS_STORAGEKEY=$(az storage account keys list -g airflow -n ${AKS_STORAGEACCT}  --query "[0].value" -o tsv)
$ kubectl create secret generic azure-secret \
  --from-literal=azurestorageaccountname=${AKS_STORAGEACCT} \
  --from-literal=azurestorageaccountkey=${AKS_STORAGEKEY}
```

Create a File Share and upload the dag file

```sh
$ az storage share create --account-name ${AKS_STORAGEACCT} --account-key ${AKS_STORAGEKEY} -n dags
$ az storage file upload --account-name ${AKS_STORAGEACCT} --account-key ${AKS_STORAGEKEY} -s dags --source dags/foobar.py --path foobar.py
$ az storage file list --account-name ${AKS_STORAGEACCT} --account-key ${AKS_STORAGEKEY} -s dags
```

Create the StorageClass, PersistentVolume and PersistentVolumeClaim

```sh
$ kubectl apply -f - <<EOF
---
kind: StorageClass
apiVersion: storage.k8s.io/v1
metadata:
  name: azurefile
provisioner: kubernetes.io/azure-file
mountOptions:
  - dir_mode=0755
  - file_mode=0755
parameters:
  skuName: Standard_LRS
  storageAccount: ${AKS_STORAGEACCT}
  resourceGroup: airflow
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: airflow-dags
  labels:
    usage: airflow-dags
spec:
  capacity:
    storage: 1Gi
  accessModes:
    - ReadOnlyMany
  storageClassName: azurefile
  azureFile:
    secretName: azure-secret
    shareName: dags
    readOnly: true
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: airflow-dags
spec:
  accessModes:
    - ReadOnlyMany
  storageClassName: azurefile
  resources:
    requests:
      storage: 1Gi
EOF
```

Make sure the pvc is bound to the volume

```sh
$ kubectl describe pvc airflow-dags
```

#### <a name="aks-test"></a>Testing Airflow [▲](#toc)

Create the service account

```sh
$ kubectl apply -f - <<'EOF'
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: airflow
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: airflow
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "watch", "list"]
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "watch", "list"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["get", "create"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
---
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: airflow
subjects:
  - kind: ServiceAccount
    name: airflow
roleRef:
  kind: Role
  name: airflow
  apiGroup: rbac.authorization.k8s.io
EOF
```

Initialize the database

```sh
$ kubectl run airflow-initdb \
    --restart=Never -ti --rm --image-pull-policy=IfNotPresent --generator=run-pod/v1 \
    --image=airflow.azurecr.io/airflow \
    --env AIRFLOW__CORE__LOAD_EXAMPLES=False \
    --env AIRFLOW__CORE__SQL_ALCHEMY_CONN="mysql://airflowmaster:airflowMa5ter@airflow.mysql.database.azure.com/airflow?ssl=true" \
    --command -- airflow initdb
```

Start the scheduler

```sh
$ kubectl run airflow -ti --rm --restart=Never --image=airflow.azurecr.io/airflow --overrides='
{
  "spec": {
    "serviceAccountName":"airflow",
    "containers":[{
      "name": "scheduler",
      "image": "airflow.azurecr.io/airflow",
      "imagePullPolicy":"IfNotPresent",
      "command": ["airflow","scheduler"],
      "stdin": true,
      "tty": true,
      "env": [
        {"name":"AIRFLOW__CORE__LOAD_EXAMPLES","value":"False"},
        {"name":"AIRFLOW__CORE__SQL_ALCHEMY_CONN","value":"mysql://airflowmaster:airflowMa5ter@airflow.mysql.database.azure.com/airflow?ssl=true"}, 
        {"name":"AIRFLOW__CORE__EXECUTOR","value":"KubernetesExecutor"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_REPOSITORY","value":"airflow.azurecr.io/airflow"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_CONTAINER_TAG","value":"latest"},
        {"name":"AIRFLOW__KUBERNETES__WORKER_SERVICE_ACCOUNT_NAME","value":"airflow"},
        {"name":"AIRFLOW__KUBERNETES__KUBE_CLIENT_REQUEST_ARGS","value":""},
        {"name":"AIRFLOW__KUBERNETES__DELETE_WORKER_PODS","value":"True"},
        {"name":"AIRFLOW__KUBERNETES__DAGS_VOLUME_CLAIM","value":"airflow-dags"}
      ],
      "volumeMounts": [{"mountPath": "/var/lib/airflow/dags","name": "dags"}]
    }],
    "volumes": [{"name":"dags","persistentVolumeClaim":{"claimName":"airflow-dags"}}]
  }
}'
```

Watch the pods

```sh
$ kubectl get po -w
```

Run the dag

```sh
$ kubectl exec -ti airflow airflow list_dags
$ kubectl exec -ti airflow airflow unpause foobar
$ kubectl exec -ti airflow airflow trigger_dag foobar
$ kubectl exec -ti airflow airflow list_dag_runs foobar
```

Here is the pods logs

```
NAME                                         READY   STATUS    RESTARTS   AGE
airflow                                      1/1     Running   0          48s
foobarbar-00d0e6c0d92c4848a45a29941035f076   1/1     Running   0          8s
foobarfoo-fb28dd8a155541379fd1a6261f41ae0b   1/1     Running   0          14s
foobarfoo-fb28dd8a155541379fd1a6261f41ae0b   0/1     Completed   0          15s
foobarfoo-fb28dd8a155541379fd1a6261f41ae0b   0/1     Terminating   0          16s
foobarfoo-fb28dd8a155541379fd1a6261f41ae0b   0/1     Terminating   0          16s
foobarbar-00d0e6c0d92c4848a45a29941035f076   0/1     Completed     0          14s
foobarbar-00d0e6c0d92c4848a45a29941035f076   0/1     Terminating   0          14s
foobarbar-00d0e6c0d92c4848a45a29941035f076   0/1     Terminating   0          14s
```

#### <a name="aks-cleanup"></a>Cleanup [▲](#toc)

Delete the resource group and remove the context

```sh
$ az group delete -g airflow
$ kubectl config delete-context airflow
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
* [Accessing Amazon RDS From AWS EKS - DEV Community 👩‍💻👨‍💻](https://dev.to/bensooraj/accessing-amazon-rds-from-aws-eks-2pc3)
* [airflow-rbac.yml](https://gist.github.com/noqcks/04d4f4a2846ec1e0ed2fbda58907ca6d)
* [pahud/amazon-eks-workshop: Amazon EKS workshop](https://github.com/pahud/amazon-eks-workshop)
* [The Ultimate Kubernetes Cost Guide: Adding Persistent Volumes to the Mix](https://www.replex.io/blog/the-ultimate-kubernetes-cost-guide-adding-persistent-storage-to-the-mix)
* [Deploying Apache Airflow on Azure Kubernetes Service](https://blog.godatadriven.com/airflow-on-aks)
* [examples/staging/volumes/azure_file at master · kubernetes/examples](https://github.com/kubernetes/examples/tree/master/staging/volumes/azure_file)