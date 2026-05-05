# DA Validation Agent

## Prerequisites
Install the necessary dependencies from the requirements file. It is recommended to perform this installation within a virtual environment (venv).

`pip install -r requirements.txt`

---

## Getting Started
## THE FOLLOWING INSTRUCTIONS ARE STRICTLY FOR MySQL DATABASE CLIENTS
###     Step 1: Clean Up Logs
        Delete all files inside the `run_logs/` directory to ensure a clean environment for the new run. If run_logs does not exist yet, ignore this step and create an empty run_logs directory.

###     Step 2: Prepare the Payload
        Rewrite the `payload_1.json` file with your specific GraphQL API Payload request.

###     Step 3: Run the Orchestrator
        Execute the neuro-symbolic orchestrator script using the following command:
`   "python neuro_symbolic_orchestrator.py --api_file "payload_1.json"`

###     Step 4: Get the Final SQL Query
        Finalize the execution by running node 4.

`python node4_advanced.py`


## THE FOLLOWING INSTRUCTIONS ARE STRICTLY FOR Postgres DATABASE CLIENTS - FANATICS
###     Step 1: Setup your .env file according to Postgres DB. This is the convention for Fanatics Client
`AZURE_API_KEY=""`\
`AZURE_ENDPOINT=""`\
`AZURE_API_VERSION=""`\
`AZURE_DEPLOYMENT_NAME=""`\
`PYTHONIOENCODING=UTF-8`\
`CREWAI_TELEMETRY_DISABLED=true`\
`RATE_LIMIT_DELAY=20`\
`DB_USER=""`\
`DB_PASSWORD=""`\
`DB_HOST=""`\
`DB_PORT=`\
`DB_TYPE=""`\
`DB_SCHEMA=""`\
`DB_DATABASE = ""`

###     Step 2: Prepare the Payload
        Rewrite the `payload.json` file with your specific GraphQL API Payload request.

###     Step 3: Run the Schema Detective
        Execute schema detective script using the following command:
`python schema_detective.py`
<br>Output will be a validated schema map which contains formulas and dependencies of all requested measures saved to `validated_schema_map.json`. Review and compare this to original API Payload.
#### Pro Tip: Save a copy of the `validated_schema_map.json` so you don't have to rerun the schema detective for the same API Payload.

###     Step 4: Get the Final SQL Query
Use `sql_pipeline.py` or `sql_architect.py` to construct final sql query.
Final validated query saved to `final_query.sql`. Be sure to save a copy of this for future use since the `final_query.sql` gets overwritten on every run.
 #### Usage Command: `python sql_pipeline.py` OR `python sql_architect.py`
