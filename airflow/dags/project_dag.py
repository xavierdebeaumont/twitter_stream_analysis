import os
from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

from schema import schema
from task_templates import (create_external_table, 
                            create_empty_table, 
                            insert_job, 
                            delete_external_table)


EVENTS = ['raw_tweets'] # we have for now one event


GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
GCP_GCS_BUCKET = os.environ.get('GCP_GCS_BUCKET')
BIGQUERY_DATASET = os.environ.get('BIGQUERY_DATASET', 'stream_stg')

EXECUTION_MONTH = '{{ logical_date.strftime("%-m") }}'
EXECUTION_DAY = '{{ logical_date.strftime("%-d") }}'
EXECUTION_HOUR = '{{ logical_date.strftime("%-H") }}'
EXECUTION_YEAR = '{{ logical_date.strftime("%-Y") }}'
EXECUTION_DATETIME_STR = '{{ logical_date.strftime("%Y%m%d%H") }}'

TABLE_MAP = { f"{event.upper()}_TABLE" : event for event in EVENTS}

MACRO_VARS = {"GCP_PROJECT_ID":GCP_PROJECT_ID, 
              "BIGQUERY_DATASET": BIGQUERY_DATASET, 
              "EXECUTION_DATETIME_STR": EXECUTION_DATETIME_STR
              }

MACRO_VARS.update(TABLE_MAP)

default_args = {
    'owner' : 'airflow'
}

with DAG(
    dag_id = f'twitter-stream_dag',
    default_args = default_args,
    description = f'Hourly data pipeline to generate dims and facts for the app',
    schedule_interval="5 * * * *", #At the 5th minute of every hour
    start_date=datetime(2022,3,26,18),
    catchup=False,
    max_active_runs=1,
    user_defined_macros=MACRO_VARS,
    tags=['twitter-streaming']
) as dag:
    
    initate_dbt_task = BashOperator(
        task_id = 'dbt_initiate',
        bash_command = 'cd /dbt/twitter_stream && dbt deps && dbt seed --select lang_names --profiles-dir . --target dev'
    )

    staging_dbt_task = BashOperator(
        task_id = 'dbt_twitter_stream_run_staging',
        bash_command = 'cd /dbt/twitter_stream && dbt deps && dbt run --models staging --profiles-dir . --target dev'
    )

    execute_dbt_task = BashOperator(
        task_id = 'dbt_twitter_stream_run_prod',
        bash_command = 'cd /dbt/twitter_stream && dbt deps && dbt run --models core --profiles-dir . --target prod'
    )

    for event in EVENTS:
        
        staging_table_name = event
        insert_query = f"{{% include 'sql/{event}.sql' %}}" #extra {} for f-strings escape
        external_table_name = f'{staging_table_name}_{EXECUTION_DATETIME_STR}'
        events_data_path = f'{staging_table_name}/year={EXECUTION_YEAR}/month={EXECUTION_MONTH}/day={EXECUTION_DAY}/hour={EXECUTION_HOUR}'
        events_schema = schema[event]

        create_external_table_task = create_external_table(event,
                                                           GCP_PROJECT_ID, 
                                                           BIGQUERY_DATASET, 
                                                           external_table_name, 
                                                           GCP_GCS_BUCKET, 
                                                           events_data_path)

        create_empty_table_task = create_empty_table(event,
                                                     GCP_PROJECT_ID,
                                                     BIGQUERY_DATASET,
                                                     staging_table_name,
                                                     events_schema)
                                                
        execute_insert_query_task = insert_job(event,
                                               insert_query,
                                               BIGQUERY_DATASET,
                                               GCP_PROJECT_ID)

        delete_external_table_task = delete_external_table(event,
                                                           GCP_PROJECT_ID, 
                                                           BIGQUERY_DATASET, 
                                                           external_table_name)
                    
        
        create_external_table_task >> \
        create_empty_table_task >> \
        execute_insert_query_task >> \
        delete_external_table_task  >> \
        initate_dbt_task >> \
        staging_dbt_task >> \
        execute_dbt_task