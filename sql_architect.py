import os
import json
from typing import TypedDict, Annotated, Sequence
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import AzureChatOpenAI

# Import our safe DB execution tool
from agent_tools import execute_read_query

load_dotenv()

llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_ENDPOINT"),
    api_key=os.getenv("AZURE_API_KEY") or os.getenv("ZURE_API_KEY"),
    api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
    azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
    temperature=0 
)

# --- 1. Structured Output Schema ---
class SubmitSQL(BaseModel):
    """Call this tool to submit your final SQL query for database execution and validation."""
    sql_query: str = Field(description="The complete PostgreSQL query, starting with WITH BaseData AS...")

# --- 2. Graph State ---
class ArchitectState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    generated_sql: str
    iteration_count: int

# --- 3. System Prompt ---
# --- 2. System Prompt ---
# Grab the exact schema from your .env file
db_schema = os.getenv("DB_SCHEMA", "public")

# --- 2. System Prompt ---
# --- 2. System Prompt ---
ARCHITECT_PROMPT = f"""
You are an elite Enterprise Data Warehouse SQL Architect.
Your job is to write a highly optimized, production-ready SQL query based EXACTLY on the provided JSON Schema Map and API Payload Context.

Use a 4-Stage CTE Architecture:

1. `BaseData` CTE (Fact Consolidation):
   - Start with `fact_data` (alias `fd`). 
   - CRITICAL: You MUST `SELECT fd.*` to ensure no dimensional routing keys are lost.
   - CRITICAL: DO NOT explicitly list individual columns from `fd` because `fd.*` already includes them! Doing so causes "ambiguous column" errors.
   - `LEFT JOIN` the `fact_override` table (alias `fo`) on core keys (`time_id`, `product_id`, `location_id`).
   - YOU MUST explicitly `SELECT` all required override columns here (e.g., `fo.user_incr_dollars_override`). Do NOT join fact tables again in later CTEs.

2. `JoinedData` CTE (Dimension Routing):
   - `SELECT bd.*` from `BaseData bd`.
   - Safely join any dimensions listed in `dimension_mappings`.
   - TIME HIERARCHY RULE: Time dimensions (like `month`) MUST route through `time_dim_xref`. 

3. `AggregatedBase` CTE (Primary Math):
   - GRAND TOTAL RULE (CRITICAL): Check the `dimensions` array in the JSON context. If the dimensions array is EMPTY (no dimensions requested), you are calculating a Grand Total. DO NOT USE A `GROUP BY` CLAUSE AT ALL.
   - If dimensions ARE requested, `GROUP BY` the descriptive name columns.
   - For metrics where `is_derived_formula` is TRUE, inject the EXACT `formula` from the JSON. 
   - ALIAS MATCHING: Replace all physical table prefixes in the JSON formulas (e.g., `fact_override.`, `fact_data.`) with `jd.` so the columns resolve correctly from the `JoinedData jd` CTE. Do NOT join `fact_override` again here!
   - ALIAS all calculations exactly as their `logical_ui_name`.

4. `FinalSelect` (Math-on-Math & Formatting):
   - Place your complex ratio/percentage formulas here (like `yoy_sales_pct_change`).
   - Use the EXACT formula string from the JSON, simply referencing the aliases you calculated in `AggregatedBase`. Do not expand them into massive SUM() statements!
   - Ensure the query ends with `LIMIT 100;`

5. FILTERING (SCOPE RULES):
   - Apply filters from the `scope` object in the `WHERE` clause of the `JoinedData` CTE.
   - Apply integer filters to the bridge table ID (e.g., `WHERE tdx.latest52_next52_id IN (1)`).

CRITICAL RULES:
- The target schema is "{db_schema}". You MUST prefix every table name in your FROM and JOIN clauses with this schema!
- Output ONLY valid SQL. Do not use markdown blocks.
"""




# --- 4. Agent Node ---
llm_with_tools = llm.bind_tools([SubmitSQL])

def agent_node(state: ArchitectState):
    print("\n🏗️ [SQL Architect is drafting/fixing the query...]")
    response = llm_with_tools.invoke(state["messages"])
    
    count = state.get("iteration_count", 0) + 1
    return {"messages": [response], "iteration_count": count}

# --- 5. Zero-Risk Database Validation Node ---
def validate_sql_node(state: ArchitectState):
    print("\n" + "="*50)
    print("🧪 [VALIDATING QUERY SYNTAX VIA 'EXPLAIN']")
    print("="*50)
    
    last_message = state["messages"][-1]
    sql_query = ""
    tool_call_id = None
    
    if last_message.tool_calls:
        for tc in last_message.tool_calls:
            if tc['name'] == 'SubmitSQL':
                sql_query = tc['args'].get("sql_query", "")
                tool_call_id = tc['id']
                break
                
    if not sql_query:
        return {"messages": [ToolMessage(content="Error: No SQL query provided.", tool_call_id=tool_call_id)]}

    print(f"\nValidating SQL Structure:\n{sql_query[:200]}...\n")
    
    # ZERO-RISK EXECUTION: Prepend EXPLAIN
    explain_query = f"EXPLAIN {sql_query}"
    result = execute_read_query(explain_query)
    
    if result and "error" in result[0]:
        error_msg = result[0]["error"]
        print(f"❌ SYNTAX/SCHEMA ERROR:\n{error_msg}")
        
        feedback = f"""
        The database rejected your query syntax with the following error:
        {error_msg}
        
        Please analyze the error, correct your SQL syntax or aliases, and submit again using `SubmitSQL`.
        """
        return {"generated_sql": sql_query, "messages": [ToolMessage(content=feedback, tool_call_id=tool_call_id)]}
    else:
        print(f"✅ SUCCESS! Query Plan generated. SQL is perfectly valid and safe to run.")
        return {"generated_sql": sql_query, "messages": [ToolMessage(content="SUCCESS! Query passed database syntax validation.", tool_call_id=tool_call_id)]}

# --- 6. Routing Logic ---
def route_agent_action(state: ArchitectState):
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "validate"
    return "end"

def route_after_validation(state: ArchitectState):
    last_message = state["messages"][-1].content
    
    if state.get("iteration_count", 0) >= 7:
        print("\n🛑 [Max iterations reached. Stopping.]")
        return "end"
        
    if "syntax validation" not in last_message:
        print("\n🔄 [Sending DB error back to Architect for self-correction...]")
        return "agent"
    
    return "end"

# --- 7. Build the LangGraph ---
workflow = StateGraph(ArchitectState)
workflow.add_node("agent", agent_node)
workflow.add_node("validate", validate_sql_node)

workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", route_agent_action, {"validate": "validate", "end": END})
workflow.add_conditional_edges("validate", route_after_validation, {"agent": "agent", "end": END})

app = workflow.compile()

# --- EXECUTION ---
if __name__ == "__main__":
    map_file = "validated_schema_map.json"
    
    if not os.path.exists(map_file):
        print(f"❌ Error: {map_file} not found! Run schema_detective.py first.")
        exit(1)
        
    with open(map_file, "r", encoding="utf-8") as f:
        schema_map = json.load(f)
        
    print("="*80)
    print("🚀 INITIALIZING SQL ARCHITECT WITH EXPLAIN VALIDATION")
    print("="*80)
    
    # Grab the exact schema from your .env file
    db_schema = os.getenv("DB_SCHEMA", "public")
    prompt_context = f"""
    Here is the validated schema map and the API Payload Context:
    
    {json.dumps(schema_map, indent=2)}
    
    CRITICAL DATABASE INSTRUCTION: 
    The target database schema is "{db_schema}". 
    You MUST prefix every single table name in your FROM and JOIN clauses with this schema name!
    Example: FROM "{db_schema}".fact_data fd
    Example: LEFT JOIN "{db_schema}".product_dim_xref pdx
    
    Please generate the final SQL query.
    """
    
    initial_state = {
        "messages": [SystemMessage(content=ARCHITECT_PROMPT), HumanMessage(content=prompt_context)],
        "generated_sql": "",
        "iteration_count": 0
    }
    
    final_state = None
    for event in app.stream(initial_state, {"recursion_limit": 20}):
        if "validate" in event:
            final_state = event["validate"]
            
    if final_state and final_state.get("generated_sql"):
        sql = final_state["generated_sql"]
        print("\n" + "="*80)
        print("🏆 FINAL VALIDATED SQL QUERY")
        print("="*80 + "\n")
        print(sql)
        
        with open("final_query.sql", "w", encoding="utf-8") as f:
            f.write(sql)
        print(f"\n💾 Saved to final_query.sql")
