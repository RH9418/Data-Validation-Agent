import os
import json
import re
from typing import TypedDict
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langchain_openai import AzureChatOpenAI
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
from agent_tools import execute_read_query

load_dotenv()

# --- LLM SETUP ---
llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_ENDPOINT"),
    api_key=os.getenv("AZURE_API_KEY") or os.getenv("ZURE_API_KEY"),
    api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
    azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
    temperature=0 
)

# --- 1. STATE DEFINITION ---
class PipelineState(TypedDict):
    schema_map: dict
    payload_context: dict
    db_schema: str
    base_ctes: str       
    aggregated_cte: str  
    final_query: str     
    current_error: str
    iteration: int
    healed_columns: list

class FoundationOutput(BaseModel):
    sql_snippet: str = Field(description="The exact SQL for the WITH BaseData AS (...) and JoinedData AS (...) CTEs.")

class AggregationOutput(BaseModel):
    sql_snippet: str = Field(description="The complete query including SELECT, FROM JoinedData jd, and GROUP BY. Do NOT wrap in CTE syntax.")

class FinalOutput(BaseModel):
    sql_snippet: str = Field(description="The complete query including SELECT ab.*, ... FROM AggregatedBase ab. Do NOT wrap in CTE syntax.")

# --- 3. AGENT 1: The Foundation Agent ---
def foundation_agent(state: PipelineState):
    print("\n🏗️ [Agent 1] Building Foundation (BaseData & JoinedData)...")
    
    db_schema = state['db_schema']
    col_query = f"SELECT column_name FROM information_schema.columns WHERE table_schema = '{db_schema}' AND table_name = 'fact_data'"
    col_res = execute_read_query(col_query)
    base_fact_keys = [r['column_name'] for r in col_res] if col_res and "error" not in col_res[0] else []
    
    dim_mappings = state['schema_map'].get('dimension_mappings', [])
    dim_joins_instructions = ""
    dim_selects = []
    
    handled_tables = {'fact_data', 'fact_override'}
    
    for d in dim_mappings:
        phys_table = d['physical_table']
        pk = d['primary_key']
        base_k = d.get('base_key', pk)
        disp = d['display_column']
        requires_bridge = d.get('requires_bridge', False)
        bridge = d.get('bridge_table', '')
        
        if base_k not in base_fact_keys:
            if any(x in base_k for x in ['month', 'time', 'date', 'week', 'year']):
                base_k = 'time_id'; requires_bridge = True; bridge = 'time_dim_xref'
            elif 'product' in base_k:
                base_k = 'product_id'; requires_bridge = True; bridge = 'product_dim_xref'

        if pk not in base_fact_keys: dim_selects.append(f"{phys_table}.{pk}")
        if disp and disp != pk and disp not in base_fact_keys: dim_selects.append(f"{phys_table}.{disp}")
            
        if requires_bridge:
            if bridge not in handled_tables:
                dim_joins_instructions += f"\n   - Explicit: LEFT JOIN \"{db_schema}\".{bridge} ON bd.{base_k} = {bridge}.{base_k}"
                handled_tables.add(bridge)
            if phys_table not in handled_tables:
                dim_joins_instructions += f"\n   - Explicit: LEFT JOIN \"{db_schema}\".{phys_table} ON {bridge}.{pk} = {phys_table}.{pk}"
                handled_tables.add(phys_table)
        else:
            if phys_table not in handled_tables:
                dim_joins_instructions += f"\n   - Explicit: LEFT JOIN \"{db_schema}\".{phys_table} ON bd.{base_k} = {phys_table}.{pk}"
                handled_tables.add(phys_table)
            
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
                implicit_joins_instructions += f"\n   - Implicit: LEFT JOIN \"{db_schema}\".{xref} ON bd.{b_k} = {xref}.{b_k}"
                handled_tables.add(xref)
            
            if xref:
                implicit_joins_instructions += f"\n   - Implicit: LEFT JOIN \"{db_schema}\".{table} ON {xref}.{pk} = {table}.{pk}"
            else:
                implicit_joins_instructions += f"\n   - Implicit: LEFT JOIN \"{db_schema}\".{table} ON bd.{b_k} = {table}.{pk}"
            handled_tables.add(table)
            dim_selects.append(f"{table}.{dep['physical_column']}")

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
                
                xref_t = f"{dim_col}_dim_xref"
                if xref_t not in handled_tables:
                    implicit_joins_instructions += f"\n   - Filter Join: LEFT JOIN \"{db_schema}\".{xref_t} ON bd.{dim_col}_id = {xref_t}.{dim_col}_id"
                    handled_tables.add(xref_t)
    else:
        filter_instructions = "\n    4. WHERE CLAUSE: None required for this query."    
    
    select_str = f"You MUST explicitly add these extra columns to your JoinedData SELECT list: {', '.join(set(dim_selects))}" if dim_selects else "No extra dimension columns needed."
    
    prompt = f"""
    You are the Foundation SQL Architect. Target Schema: "{state['db_schema']}"
    
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
    
    Output ONLY the valid SQL for these two CTEs. DO NOT output a final SELECT statement (e.g. DO NOT write `SELECT * FROM JoinedData`). DO NOT add trailing comments like `-- End of SQL`.
    """
    
    if state.get("current_error"):
        prompt += f"\n\nCRITICAL FIX REQUIRED:\n{state['current_error']}\nYou used a column that doesn't exist, or created an ambiguous column."
        
    structured_llm = llm.with_structured_output(FoundationOutput)
    res = structured_llm.invoke(prompt)
    
    # --- RESTORED: Aggressive Regex Stripping for trailing comments and SELECTs ---
    clean_sql = res.sql_snippet.strip().rstrip(';')
    clean_sql = re.sub(r'(?i)\n*SELECT\s+\*\s+FROM\s+JoinedData\s*;?$', '', clean_sql).strip()
    clean_sql = re.sub(r'--.*$', '', clean_sql, flags=re.MULTILINE).strip()
    
    print("\n--- 🐞 DEBUG: AGENT 1 OUTPUT ---")
    print(clean_sql)
    print("--------------------------------\n")
    
    return {"base_ctes": clean_sql, "iteration": state.get("iteration", 0) + 1}

def validate_foundation(state: PipelineState):
    print("   🧪 Validating Foundation...")
    # --- RESTORED: \n added to prevent swallowing by rogue comments ---
    test_query = f"EXPLAIN {state['base_ctes']}\nSELECT * FROM JoinedData LIMIT 1;"
    res = execute_read_query(test_query)
    
    if res and "error" in res[0]:
        error_msg = res[0]["error"].split("HINT:")[0].strip()
        print(f"   ❌ Error: {error_msg}")
        return {"current_error": error_msg}
        
    print("   ✅ Foundation Validated Successfully!")
    return {"current_error": "", "iteration": 0}

# --- 4. AGENT 2: The Aggregation Agent ---
def aggregation_agent(state: PipelineState):
    print("\n📊 [Agent 2] Building Aggregations (AggregatedBase)...")
    
    mappings = state['schema_map'].get('mappings', [])
    
    # SAFE REGEX SANITIZATION & FORMULA AUTO-GENERATION
    sanitized_mappings = []
    for m in mappings:
        new_m = m.copy()
        
        # Fix known payload typos
        if new_m.get('formula'):
            new_m['formula'] = new_m['formula'].replace('product3_ids', 'product3_id')
            
        # Auto-generate formulas for BASE_ONLY metrics
        if not new_m.get('formula') and new_m.get('metric_stage') == 'BASE_ONLY' and new_m.get('dependencies'):
            alias = new_m['logical_ui_name']
            dep_col = new_m['dependencies'][0]['physical_column']
            if alias.startswith('sum_'): new_m['formula'] = f"SUM({dep_col})"
            elif alias.startswith('avg_'): new_m['formula'] = f"AVG({dep_col})"
            elif alias.startswith('min_'): new_m['formula'] = f"MIN({dep_col})"
            elif alias.startswith('max_'): new_m['formula'] = f"MAX({dep_col})"
            elif alias.startswith('count_'): new_m['formula'] = f"COUNT({dep_col})"
            else: new_m['formula'] = f"MAX({dep_col})"
        
        # Strip table prefixes safely
        if new_m.get('formula'):
            clean_form = re.sub(r'\b[a-zA-Z_][a-zA-Z0-9_]*\.', '', new_m['formula'])
            new_m['formula'] = clean_form
            
        sanitized_mappings.append(new_m)
        
    agent_2_metrics = [m for m in sanitized_mappings if m.get('metric_stage') in ['STANDARD_AGGREGATION', 'BASE_ONLY']]
    
    ghost_aliases = set()
    for m in sanitized_mappings:
        if m.get('metric_stage') == 'MATH_ON_MATH':
            for alias in m.get('required_intermediate_aliases', []):
                ghost_aliases.add(alias)
                
    # AUTO-HEAL STATE PERSISTENCE
    healed_columns = state.get("healed_columns", [])
    err = state.get("current_error", "")
    if err and "does not exist" in err:
        match = re.search(r'column (?:ab\.)?([a-z0-9_]+) does not exist', err)
        if match:
            missing_col = match.group(1)
            if missing_col not in healed_columns:
                healed_columns.append(missing_col)
            print(f"   🔧 [Auto-Heal] Extracted missing column '{missing_col}'. Persisting to memory.")

    for col in healed_columns:
        ghost_aliases.add(col)

    standard_ui_names = [m['logical_ui_name'] for m in agent_2_metrics]
    checklist = list(set(standard_ui_names + list(ghost_aliases)))
    
    dim_mappings = state['schema_map'].get('dimension_mappings', [])
    
    if dim_mappings:
        group_by_cols = []
        for d in dim_mappings:
            pk = d['primary_key']
            disp = d['display_column']
            group_by_cols.append(f"jd.{pk}")
            if disp and disp != pk:
                 group_by_cols.append(f"jd.{disp}")
                 
        select_prefix = f"{', '.join(group_by_cols)}, "
        group_by_clause = f"You MUST INCLUDE exactly this group by clause: `GROUP BY {', '.join(group_by_cols)}`"
    else:
        select_prefix = ""
        group_by_clause = "DO NOT INCLUDE A GROUP BY CLAUSE. This is a Grand Total query."
    
    # FORCE ALL BASE METRICS AND GHOST ALIASES
    explicit_metric_instructions = ""
    
    for m in agent_2_metrics:
        if m.get('metric_stage') == 'BASE_ONLY':
            alias = m['logical_ui_name']
            deps = m.get('dependencies', [])
            dep_col = deps[0]['physical_column'] if deps else alias.replace('sum_', '').replace('avg_', '').replace('min_', '').replace('max_', '')
            
            if alias == 'row_count': form = "COUNT(*)"
            elif alias.startswith('sum_'): form = f"SUM(jd.{dep_col})"
            elif alias.startswith('avg_'): form = f"AVG(jd.{dep_col})"
            elif alias.startswith('min_'): form = f"MIN(jd.{dep_col})"
            elif alias.startswith('max_'): form = f"MAX(jd.{dep_col})"
            elif alias.startswith('count_'): form = f"COUNT(jd.{dep_col})"
            else: form = f"MAX(jd.{dep_col})"
            explicit_metric_instructions += f"    {form} AS {alias},\n"
            
    for alias in ghost_aliases:
        if alias in standard_ui_names: continue 
        if alias == 'row_count': form = "COUNT(*)"
        elif alias.startswith('sum_'): form = f"SUM(jd.{alias[4:]})"
        elif alias.startswith('avg_'): form = f"AVG(jd.{alias[4:]})"
        elif alias.startswith('min_'): form = f"MIN(jd.{alias[4:]})"
        elif alias.startswith('max_'): form = f"MAX(jd.{alias[4:]})"
        elif alias.startswith('count_'): form = f"COUNT(jd.{alias[6:]})"
        else: form = f"MAX(jd.{alias})"
        explicit_metric_instructions += f"    {form} AS {alias},\n"
    
    prompt = f"""
    You are the Aggregation SQL Architect. Target Schema: "{state['db_schema']}"
    
    Write the COMPLETE inner query that will query from `JoinedData jd`.
    
    JSON Context (Metrics to aggregate):
    {json.dumps(agent_2_metrics, indent=2)}
    
    RULES:
    1. START DIRECTLY WITH `SELECT {select_prefix}` followed by your aggregated metrics.
    2. YOU MUST INCLUDE `FROM JoinedData jd` after your SELECT list.
    3. GROUP BY RULE (CRITICAL): {group_by_clause}
    4. Apply aggregations (SUM, AVG, MIN, MAX) for raw metrics. 
       - HINT: If a JSON formula uses an alias (like `min_date_ly`), expand it using jd (e.g., `MIN(jd.date_ly) AS min_date_ly`).
    5. BASE METRICS & GHOST ALIASES (CRITICAL): You MUST copy and paste these EXACT lines into your SELECT statement:
{explicit_metric_instructions}
    
    ANTI-LAZINESS CHECKLIST (CRITICAL):
    You MUST explicitly aggregate/select every single metric and intermediate alias listed below. DO NOT TRUNCATE!
    Checklist: {', '.join(checklist)}
    """
    
    if err:
        clean_err = err.replace("ab.", "")
        prompt += f"\n\nCRITICAL ERROR FEEDBACK:\nValidation failed: {clean_err}\nWe extracted the missing column and added it to the rules above. Ensure you copy ALL explicit metrics!"
        
    structured_llm = llm.with_structured_output(AggregationOutput)
    res = structured_llm.invoke(prompt).sql_snippet.strip().rstrip(';')
    
    clean_cte = f"AggregatedBase AS (\n{res}\n)"
    
    print("\n--- 🐞 DEBUG: AGENT 2 OUTPUT ---")
    print(clean_cte)
    print("--------------------------------\n")
    
    return {"aggregated_cte": clean_cte, "iteration": state.get("iteration", 0) + 1, "healed_columns": healed_columns}

def validate_aggregation(state: PipelineState):
    print("   🧪 Validating Aggregations...")
    test_query = f"EXPLAIN {state['base_ctes']},\n{state['aggregated_cte']}\nSELECT * FROM AggregatedBase LIMIT 1;"
    res = execute_read_query(test_query)
    
    if res and "error" in res[0]:
        error_msg = res[0]["error"].split("HINT:")[0].strip()
        print(f"   ❌ Error: {error_msg}")
        return {"current_error": error_msg}
        
    print("   ✅ Aggregations Validated Successfully!")
    return {"current_error": "", "iteration": 0}

# --- 5. AGENT 3: The Finalization Agent ---
def finalization_agent(state: PipelineState):
    print("\n🏁 [Agent 3] Building Final Math-on-Math (FinalSelect)...")
    
    mappings = state['schema_map'].get('mappings', [])
    
    sanitized_mappings = []
    for m in mappings:
        new_m = m.copy()
        if new_m.get('formula'):
            clean_form = new_m['formula'].replace('product3_ids', 'product3_id')
            clean_form = re.sub(r'\b[a-zA-Z_][a-zA-Z0-9_]*\.', '', clean_form)
            new_m['formula'] = clean_form
        sanitized_mappings.append(new_m)
        
    agent_3_metrics = [m for m in sanitized_mappings if m.get('metric_stage') == 'MATH_ON_MATH']
    
    prompt = f"""
    You are the Finalization SQL Architect.
    
    Write the COMPLETE inner query that queries from `AggregatedBase ab`.
    
    JSON Context (Math-on-Math Metrics ONLY):
    {json.dumps(agent_3_metrics, indent=2)}
    
    RULES:
    1. START DIRECTLY WITH `SELECT ab.*, `
    2. YOU MUST INCLUDE `FROM AggregatedBase ab` at the end of your query!
    3. Calculate the complex ratio/percentage formulas ONLY, referencing the `ab.` aliases. DO NOT use SUM() or AVG() here.
    4. For Window Functions (OVER PARTITION BY), reference the `ab.` aliases.
    """
    
    if state.get("current_error"):
        prompt += f"\n\nFIX THIS PREVIOUS ERROR:\n{state['current_error']}\nHINT: Fix the missing column alias or window function syntax!"
        
    structured_llm = llm.with_structured_output(FinalOutput)
    res = structured_llm.invoke(prompt).sql_snippet.strip().rstrip(';')
    
    sort_data = state['payload_context'].get('sort') or {}
    sort_entries = sort_data.get('entries', [])
    order_by_clause = ""
    
    if sort_entries:
        order_parts = []
        for entry in sort_entries:
            col = entry.get('columnName')
            direction = entry.get('direction', 'ASC')
            if entry.get('columnType') == 'NAME':
                col = f"{col}_name" 
            else:
                col = f"{col}_id" 
            order_parts.append(f"{col} {direction}")
        order_by_clause = f"\nORDER BY {', '.join(order_parts)}"
        
    limit_val = state['payload_context'].get('first')
    offset_val = state['payload_context'].get('after')
    
    pagination_clause = ""
    if limit_val is not None:
        pagination_clause += f"\nLIMIT {limit_val}"
    if offset_val is not None and str(offset_val).isdigit():
        pagination_clause += f" OFFSET {offset_val}"
        
    if not pagination_clause:
        pagination_clause = "\nLIMIT 100" 
    
    final_sql = f"{state['base_ctes']},\n{state['aggregated_cte']},\nFinalSelect AS (\n{res}\n)\nSELECT * FROM FinalSelect{order_by_clause}{pagination_clause};"
    
    print("\n--- 🐞 DEBUG: AGENT 3 OUTPUT ---")
    print(final_sql)
    print("--------------------------------\n")
    
    return {"final_query": final_sql, "iteration": state.get("iteration", 0) + 1}

def validate_final(state: PipelineState):
    print("   🧪 Validating Final Query...")
    test_query = f"EXPLAIN {state['final_query']}"
    res = execute_read_query(test_query)
    if res and "error" in res[0]:
        error_msg = res[0]["error"].split("HINT:")[0].strip()
        print(f"   ❌ Error: {error_msg}")
        return {"current_error": error_msg}
    print("   ✅ Complete Query Validated!")
    return {"current_error": ""}

# --- 6. LangGraph Routing & Build ---
def route_foundation(state):
    if state.get("iteration", 0) > 8: return END
    return "agent_2" if not state.get("current_error") else "agent_1"

def route_aggregation(state):
    if state.get("iteration", 0) > 12: return END
    if not state.get("current_error"): return "agent_3"
    
    err = state.get("current_error", "").lower()
    if "does not exist" in err and "missing from-clause" not in err:
        print("\n🔄 [Backward Propagation: Agent 1 forgot to SELECT a column. Routing back to Agent 1...]")
        return "agent_1"
        
    print("\n🔄 [Agent 2 Syntax Error. Routing back to Agent 2...]")
    return "agent_2"

def route_final(state):
    if state.get("iteration", 0) > 15: return END
    if not state.get("current_error"): return END
    
    err = state.get("current_error", "").lower()
    if "does not exist" in err and "missing from-clause" not in err:
        print("\n🔄 [Backward Propagation: Agent 2 forgot an alias. Routing back to Agent 2...]")
        return "agent_2"
        
    print("\n🔄 [Agent 3 Syntax Error. Routing back to Agent 3...]")
    return "agent_3"

workflow = StateGraph(PipelineState)
workflow.add_node("agent_1", foundation_agent)
workflow.add_node("val_1", validate_foundation)
workflow.add_node("agent_2", aggregation_agent)
workflow.add_node("val_2", validate_aggregation)
workflow.add_node("agent_3", finalization_agent)
workflow.add_node("val_3", validate_final)

workflow.set_entry_point("agent_1")
workflow.add_edge("agent_1", "val_1")
workflow.add_conditional_edges("val_1", route_foundation, {"agent_1": "agent_1", "agent_2": "agent_2", END: END})
workflow.add_edge("agent_2", "val_2")
workflow.add_conditional_edges("val_2", route_aggregation, {"agent_1": "agent_1", "agent_2": "agent_2", "agent_3": "agent_3", END: END})
workflow.add_edge("agent_3", "val_3")
workflow.add_conditional_edges("val_3", route_final, {"agent_2": "agent_2", "agent_3": "agent_3", END: END})

app = workflow.compile()

# --- EXECUTION ---
if __name__ == "__main__":
    map_file = "validated_schema_map.json"
    
    if not os.path.exists(map_file):
        print(f"❌ Error: {map_file} not found!")
        exit(1)
        
    with open(map_file, "r", encoding="utf-8") as f:
        schema_map = json.load(f)
        
    print("="*80)
    print("🚀 INITIALIZING MULTI-AGENT SQL PIPELINE")
    print("="*80)
    
    initial_state = {
        "schema_map": schema_map.get("validated_mappings", {}),
        "payload_context": schema_map.get("api_payload_context", {}),
        "db_schema": os.getenv("DB_SCHEMA", "public"),
        "base_ctes": "", "aggregated_cte": "", "final_query": "",
        "current_error": "", "iteration": 0, "healed_columns": []
    }
    
    final_state = app.invoke(initial_state, {"recursion_limit": 50})
            
    if final_state and not final_state.get("current_error"):
        print("\n" + "="*80)
        print("🏆 FINAL VALIDATED SQL QUERY")
        print("="*80 + "\n")
        
        final_sql = final_state.get("final_query", "Error: final_query not found in state.")
        print(final_sql)
        
        with open("final_query.sql", "w", encoding="utf-8") as f:
            f.write(final_sql)
        print(f"\n💾 Saved to final_query.sql")
    else:
        print("\n❌ Pipeline failed to resolve errors within limits.")
