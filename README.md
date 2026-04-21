# DA Validation Agent

## Prerequisites
Install the necessary dependencies from the requirements file. It is recommended to perform this installation within a virtual environment (venv).

`pip install -r requirements.txt`

---

## Getting Started

### Step 1: Clean Up Logs
Delete all files inside the `run_logs/` directory to ensure a clean environment for the new run. If run_logs does not exist yet, ignore this step and create an empty run_logs directory.

### Step 2: Prepare the Payload
Rewrite the `payload_1.json` file with your specific GraphQL API Payload request.

### Step 3: Run the Orchestrator
Execute the neuro-symbolic orchestrator script using the following command:

`python neuro_symbolic_orchestrator.py --api_file "payload_1.json"`

### Step 4: Get the Final SQL Query
Finalize the execution by running node 4.

`python node4_advanced.py`
