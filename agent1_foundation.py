import os
import json
from typing import TypedDict
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langchain_openai import AzureChatOpenAI
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
from agent_tools import execute_read_query

load_dotenv()

llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_ENDPOINT"),
    api_key=os.getenv("AZURE_API_KEY") or os.getenv("ZURE_API_KEY"),
    api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
    azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
    temperature=0 
)

class Agent1State(TypedDict):
    schema_map: dict
    payload_context: dict
    db_schema: str
    base_ctes: str       
    current_error: str
    iteration: int

class FoundationOutput(BaseModel):
    sql_snippet: str = Field(description="The exact SQL for the WITH BaseData AS (...) and JoinedData AS (...) CTEs.")

def foundation_agent(state: Agent1State):
    print("\n🏗️ [Agent 1] Building Foundation (BaseData & JoinedData)...")
    
    db_schema = state['db_schema']
    col_query = f"SELECT column_name FROM information_schema.columns WHERE table_schema = '{db_schema}' AND table_name = 'fact_data'"
    col_res = execute_read_query(col_query)
    base_fact_keys = [r['column_name'] for r in col_res] if col_res and "error" not in col_res[0] else []

    dim_mappings = state['schema_map'].get('dimension_mappings', [])
    dim_joins_instructions = ""
    dim_selects = []
    
    # Keep track of what we've already joined so we don't duplicate
    handled_tables = {'fact_data', 'fact_override'}
    
    # 1. EXPLICIT DIMENSIONS
    for d in dim_mappings:
        phys_table = d['physical_table']
        pk = d['primary_key']
        base_k = d.get('base_key', pk)
        disp = d['display_column']
        requires_bridge = d.get('requires_bridge', False)
        bridge = d.get('bridge_table', '')
        
        # Smart Override if JSON is bad
        if base_k not in base_fact_keys:
            if any(x in base_k for x in ['month', 'time', 'date', 'week', 'year']):
                base_k = 'time_id'
                requires_bridge = True
                bridge = 'time_dim_xref'
            elif 'product' in base_k:
                base_k = 'product_id'
                requires_bridge = True
                bridge = 'product_dim_xref'

        if pk not in base_fact_keys: dim_selects.append(f"{phys_table}.{pk}")
        if disp and disp != pk and disp not in base_fact_keys: dim_selects.append(f"{phys_table}.{disp}")
            
        if requires_bridge:
            dim_joins_instructions += f"\n   - Explicit: LEFT JOIN {bridge} ON bd.{base_k} = {bridge}.{base_k} LEFT JOIN {phys_table} ON {bridge}.{pk} = {phys_table}.{pk}"
            handled_tables.update([bridge, phys_table])
        else:
            dim_joins_instructions += f"\n   - Explicit: LEFT JOIN {phys_table} ON bd.{base_k} = {phys_table}.{pk}"
            handled_tables.add(phys_table)
            
    # 2. IMPLICIT DEPENDENCIES (The Fix for the 'month_id' crash)
    all_deps = []
    for m in state['schema_map'].get('mappings', []): all_deps.extend(m.get('dependencies', []))
    unique_deps_list = list({f"{d['physical_table']}.{d['physical_column']}": d for d in all_deps if d}.values())
    
    override_cols = [f"fo.{d['physical_column']}" for d in unique_deps_list if d['physical_table'] == 'fact_override']
    
    implicit_joins_instructions = ""
    for dep in unique_deps_list:
        table = dep['physical_table']
        if table not in handled_tables and table != 'NOT_FOUND':
            if any(x in table for x in ['month', 'time', 'date', 'week', 'year']):
                b_k = 'time_id'; xref = 'time_dim_xref'
            elif 'product' in table:
                b_k = 'product_id'; xref = 'product_dim_xref'
            elif 'location' in table:
                b_k = 'location_id'; xref = 'location_dim_xref'
            else:
                b_k = table.replace('_dim_desc', '_id'); xref = None

            pk = table.replace('_dim_desc', '_id') if '_dim_desc' in table else f"{table}_id"

            if xref and xref not in handled_tables:
                implicit_joins_instructions += f"\n   - Implicit: LEFT JOIN {xref} ON bd.{b_k} = {xref}.{b_k} LEFT JOIN {table} ON {xref}.{pk} = {table}.{pk}"
                handled_tables.update([xref, table])
            elif xref and xref in handled_tables:
                implicit_joins_instructions += f"\n   - Implicit: LEFT JOIN {table} ON {xref}.{pk} = {table}.{pk}"
                handled_tables.add(table)
            elif not xref:
                implicit_joins_instructions += f"\n   - Implicit: LEFT JOIN {table} ON bd.{b_k} = {table}.{pk}"
                handled_tables.add(table)
            
            dim_selects.append(f"{table}.{dep['physical_column']}")

    # 3. FILTER JOINS
    filters = state['payload_context'].get('filters', {}).get('dimensionFilters', [])
    filter_instructions = ""
    if filters:
        filter_instructions = "\n    4. WHERE CLAUSE (CRITICAL): You MUST apply these filters:\n"
        for f in filters:
            dim_col = f.get('dimensionColumnName') 
            for and_cond in f.get('and', []):
                lvl_col = and_cond.get('dimensionLevelColumnName')
                vals = and_cond.get('values', [])
                
                is_numeric = all(isinstance(v, (int, float)) for v in vals)
                phys_col = lvl_col
                if is_numeric and not phys_col.endswith('_id'): phys_col = f"{phys_col}_id"
                
                formatted_vals = [f"'{v}'" if isinstance(v, str) else str(v) for v in vals]
                filter_instructions += f"       - FILTER: `{dim_col}_dim_xref.{phys_col} IN ({', '.join(formatted_vals)})`\n"
                
                # Make sure the xref is joined!
                xref_t = f"{dim_col}_dim_xref"
                if xref_t not in handled_tables:
                    implicit_joins_instructions += f"\n   - Filter Join: LEFT JOIN {xref_t} ON bd.{dim_col}_id = {xref_t}.{dim_col}_id"
                    handled_tables.add(xref_t)
    else:
        filter_instructions = "\n    4. WHERE CLAUSE: None required for this query."    
    
    select_str = f"You MUST explicitly add these extra columns to your JoinedData SELECT list: {', '.join(set(dim_selects))}" if dim_selects else "No extra dimension columns needed."
    
    prompt = f"""
    You are the Foundation SQL Architect. Target Schema: "{state['db_schema']}"
    Your ONLY job is to write the first two CTEs.
    
    RULES FOR BaseData:
    1. `WITH BaseData AS (...)`
    2. `SELECT fd.*{', ' + ', '.join(override_cols) if override_cols else ''}` from `"{state['db_schema']}".fact_data fd`.
    3. `LEFT JOIN "{state['db_schema']}".fact_override fo ON fd.time_id = fo.time_id AND fd.product_id = fo.product_id AND fd.location_id = fo.location_id`
    
    RULES FOR JoinedData (CRITICAL):
    1. `JoinedData AS (...)` -> `SELECT bd.*` from `BaseData bd`.
    2. {select_str}
    3. EXPLICIT JOINS: You MUST copy and paste these exact lines for the table joins:
    {dim_joins_instructions}
    {implicit_joins_instructions}
    {filter_instructions}
    
    Output ONLY the valid SQL for these two CTEs.
    """
    
    if state.get("current_error"):
        prompt += f"\n\nCRITICAL FIX REQUIRED:\n{state['current_error']}\nYou used a column that doesn't exist, or created an ambiguous column."
        
    structured_llm = llm.with_structured_output(FoundationOutput)
    res = structured_llm.invoke(prompt)
    
    print("\n--- 🐞 DEBUG: AGENT 1 OUTPUT ---")
    print(res.sql_snippet)
    print("--------------------------------\n")
    
    return {"base_ctes": res.sql_snippet, "iteration": state.get("iteration", 0) + 1}

def validate_foundation(state: Agent1State):
    print("   🧪 Validating Foundation...")
    test_query = f"EXPLAIN {state['base_ctes']} SELECT * FROM JoinedData LIMIT 1;"
    res = execute_read_query(test_query)
    
    if res and "error" in res[0]:
        error_msg = res[0]["error"].split("HINT:")[0].strip()
        print(f"   ❌ Error: {error_msg}")
        return {"current_error": error_msg}
        
    print("   ✅ Foundation Validated Successfully!")
    return {"current_error": "", "iteration": 0}

def route_foundation(state: Agent1State):
    if state.get("iteration", 0) > 5: 
        return END 
    return END if not state.get("current_error") else "agent_1"

workflow = StateGraph(Agent1State)
workflow.add_node("agent_1", foundation_agent)
workflow.add_node("val_1", validate_foundation)
workflow.set_entry_point("agent_1")
workflow.add_edge("agent_1", "val_1")
workflow.add_conditional_edges("val_1", route_foundation, {"agent_1": "agent_1", END: END})
app = workflow.compile()

if __name__ == "__main__":
    map_file = "validated_schema_map.json"
    with open(map_file, "r", encoding="utf-8") as f:
        schema_map = json.load(f)
        
    print("="*80)
    print("🚀 INITIALIZING STANDALONE AGENT 1 (FOUNDATION)")
    print("="*80)
    
    initial_state = {
        "schema_map": schema_map.get("validated_mappings", {}),
        "payload_context": schema_map.get("api_payload_context", {}),
        "db_schema": os.getenv("DB_SCHEMA", "public"),
        "base_ctes": "", "current_error": "", "iteration": 0
    }
    
    final_state = app.invoke(initial_state, {"recursion_limit": 20})
            
    if not final_state.get("current_error"):
        print("\n" + "="*80)
        print("🏆 FINAL VALIDATED CTEs")
        print("="*80 + "\n")
        print(final_state.get("base_ctes"))
    else:
        print("\n❌ Agent 1 failed to resolve errors within limits.")
