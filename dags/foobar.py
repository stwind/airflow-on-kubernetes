from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from datetime import datetime

default_args = {
    "start_date": datetime(2015, 6, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 0,
}

dag = DAG("foobar", default_args=default_args, schedule_interval=None, catchup=False)

t1 = BashOperator(task_id="foo", bash_command="echo foo", dag=dag)
t2 = BashOperator(task_id="bar", bash_command="echo bar", dag=dag)

t2.set_upstream(t1)
