# import os
# import json
# import argparse
# import sqlglot
# import sqlglot.expressions as exp
# from typing import TypedDict, Dict, Any

# from langgraph.graph import StateGraph, END
# from dotenv import load_dotenv
# from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

# load_dotenv()

# # --- 📝 OBSERVABILITY SETUP ---
# LOG_DIR = "run_logs"
# os.makedirs(LOG_DIR, exist_ok=True)
# CACHE_FILE = os.path.join(LOG_DIR, "semantic_cache.json")
# JOIN_CACHE_FILE = os.path.join(LOG_DIR, "join_cache.json")

# def dump_log(filename: str, content: str):
#     filepath = os.path.join(LOG_DIR, filename)
#     with open(filepath, "w", encoding="utf-8") as f:
#         f.write(content)

# def clean_physical_column(col):
#     if not col or str(col).strip() == "*": return "*"
#     c = str(col).split('.')[-1].lower().strip()
#     if c.startswith("sum_"): return c[4:]
#     return c

# # --- GRAPH STATE ---
# class PipelineState(TypedDict):
#     api_payload: Dict[str, Any]
#     extracted_intent: Dict[str, Any]
#     resolved_semantics: Dict[str, Any]
#     join_paths: Dict[str, Any]
#     error_message: str

# # --- 🚀 NODE 1: INTENT EXTRACTION ---
# def node_extract_intent(state: PipelineState):
#     print("\n" + "="*60 + "\n🚀 NODE 1: EXTRACT INTENT\n" + "="*60)
#     payload = state["api_payload"]
#     dump_log("00_raw_api_payload.json", json.dumps(payload, indent=2))
    
#     variables = payload.get("variables", payload)
#     query_block = variables.get("query")
#     if not isinstance(query_block, dict): query_block = {}
    
#     def ensure_list(item):
#         if not item: return []
#         if isinstance(item, list): return item
#         return [item]
        
#     dim_levels = ensure_list(query_block.get("dimensionLevels"))
#     agg_measures = ensure_list(query_block.get("aggregatedMeasures"))
#     datatables = ensure_list(query_block.get("datatable") or variables.get("datatable"))
    
#     filter_cols, raw_filters = [], []
#     filters = query_block.get("scope", {})
#     if filters and "dimensionFilters" in filters:
#         for f in ensure_list(filters.get("dimensionFilters")):
#             raw_filters.append(f)
#             if isinstance(f, dict) and "dimensionColumnName" in f: filter_cols.append(f["dimensionColumnName"])
#             for cond in ensure_list(f.get("and", [])):
#                 if isinstance(cond, dict) and "dimensionLevelColumnName" in cond: filter_cols.append(cond["dimensionLevelColumnName"])
                    
#     direct_cols = ensure_list(variables.get("columnsToFetch"))
#     for df in ensure_list(variables.get("filters")):
#         if isinstance(df, dict) and "columnName" in df: 
#             filter_cols.append(df["columnName"])
#             raw_filters.append({
#                 "dimensionColumnName": df["columnName"],
#                 "and": [{"dimensionLevelColumnName": df["columnName"], "cmpOperator": df.get("operator", "EQ"), "values": df.get("value", [])}]
#             })
                    
#     sort_cols = []
#     seen_sorts = set()
#     for s in ensure_list((query_block.get("sort") or {}).get("entries", [])):
#         if isinstance(s, dict) and "columnName" in s:
#             col = s["columnName"]
#             if col not in seen_sorts:
#                 sort_cols.append({"columnName": col, "direction": s.get("direction", "ASC").upper()})
#                 seen_sorts.add(col)
        
#     limit_rows = query_block.get("first") or variables.get("first") or 200
#     is_direct_fetch = len(direct_cols) > 0
#     is_dim_member_fetch = len(dim_levels) > 0 and len(agg_measures) == 0

#     intent = {
#         "is_direct_fetch": is_direct_fetch,
#         "is_dim_member_fetch": is_dim_member_fetch,
#         "target_tables": datatables, 
#         "dimensions": list(set(dim_levels)),
#         "measures": list(set(agg_measures)), 
#         "filter_columns": list(set(filter_cols)),
#         "sort_columns": sort_cols, 
#         "direct_columns": list(set(direct_cols)),
#         "raw_filters": raw_filters,
#         "limit": limit_rows
#     }
    
#     dump_log("01_extracted_intent.json", json.dumps(intent, indent=2))
    
#     if is_direct_fetch: print("✅ Intent Extracted: DIRECT FETCH QUERY DETECTED.")
#     elif is_dim_member_fetch: print("✅ Intent Extracted: PILL DROPDOWN QUERY DETECTED.")
#     else: print("✅ Intent Extracted: FACT AGGREGATION DETECTED.")
        
#     return {"extracted_intent": intent, "error_message": ""}

# # --- 🧠 NODE 2: SEMANTIC RESOLVER ---
# def load_cache():
#     if os.path.exists(CACHE_FILE):
#         with open(CACHE_FILE, "r", encoding="utf-8") as f:
#             try: return json.load(f)
#             except json.JSONDecodeError: return {}
#     return {}

# def save_cache(cache_data):
#     os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
#     with open(CACHE_FILE, "w", encoding="utf-8") as f:
#         json.dump(cache_data, f, indent=4)

# def extract_columns_from_formula(formula_string):
#     if not formula_string: return []
#     try:
#         tree = sqlglot.parse_one(formula_string)
#         return list(set([node.name for node in tree.find_all(exp.Column)]))
#     except Exception: return []

# def node_semantic_resolver(state: PipelineState):
#     print("\n" + "="*60 + "\n🧠 NODE 2: SEMANTIC RESOLVER\n" + "="*60)
#     intent = state["extracted_intent"]
#     requested_measures = intent.get("measures", [])
    
#     if not requested_measures:
#         print("⏭️ No measures to resolve. Skipping Semantic Graph traversal.")
#         dump_log("02_resolved_semantics.json", json.dumps({}, indent=2))
#         return {"resolved_semantics": {}}

#     cache = load_cache()
#     resolved = {}
#     missing_measures = []

#     for m in requested_measures:
#         if m in cache: resolved[m] = cache[m]
#         else: missing_measures.append(m)

#     if not missing_measures:
#         print("⚡ 100% Cache Hit! Zero Database queries required.")
#         dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
#         return {"resolved_semantics": resolved}

#     print(f"🔍 Cache Miss for {len(missing_measures)} measures. Traversing BI Metadata Graph...")
    
#     conn = get_db_connection()
#     if isinstance(conn, str):
#         print(f"🔥 Database Connection Error: {conn}")
#         return {"resolved_semantics": resolved}
    
#     try:
#         cursor = get_dict_cursor(conn)
        
#         tables_to_check = ['measures', 'measure_aggregations', 'measure_aggregation_dependencies']
#         found_tables = {}

#         for t in tables_to_check:
#             cursor.execute(f"SHOW TABLES LIKE '%{t}%'")
#             res = cursor.fetchall()
#             if res:
#                 found_tables[t] = list(res[0].values())[0]

#         ma_table = found_tables.get('measure_aggregations')
#         mad_table = found_tables.get('measure_aggregation_dependencies')
#         m_table = found_tables.get('measures')

#         if not ma_table:
#             print("⚠️ No metadata tables found. Falling back to direct mappings.")
#             for m in missing_measures: 
#                 clean_m = clean_physical_column(m)
#                 resolved[m] = {"formula": clean_m, "physical_columns": [clean_m], "type": "SUM"}
            
#             cache.update(resolved)
#             save_cache(cache)
#             dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
#             return {"resolved_semantics": resolved}

#         print(f"✅ BI Metadata Tables Located! Using: [{ma_table}]")

#         new_discoveries = 0
#         for measure in missing_measures:
#             if measure.lower() == 'row_count':
#                 resolved[measure] = {"formula": "COUNT(*)", "physical_columns": ["*"], "type": "COUNT"}
#                 new_discoveries += 1
#                 continue
#             if measure.lower() in ['is_editable', 'pt_editable', 'imputed_flag', 'planner_tag']:
#                 resolved[measure] = {"formula": "NULL", "physical_columns": [], "type": "FORMULA"}
#                 new_discoveries += 1
#                 continue

#             cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_column_name = %s LIMIT 1", (measure,))
#             row = cursor.fetchone()
            
#             if not row:
#                 clean_m = clean_physical_column(measure)
#                 resolved[measure] = {"formula": clean_m, "physical_columns": [clean_m], "type": "SUM"}
#                 new_discoveries += 1
#                 continue

#             curr_id = row.get('measure_aggregation_id', row.get('MEASURE_AGGREGATION_ID'))
#             raw_formula = row.get('measure_formula', row.get('MEASURE_FORMULA', ''))
#             m_type = str(row.get('measure_aggregation_type', row.get('MEASURE_AGGREGATION_TYPE', 'SUM'))).upper()

#             if m_type != "FORMULA":
#                 m_fk = row.get('measure_id', row.get('MEASURE_ID'))
#                 if m_fk and m_table:
#                     cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (m_fk,))
#                     m_row = cursor.fetchone()
#                     if m_row:
#                         phys_col = m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME'))
#                         resolved[measure] = {"formula": phys_col, "physical_columns": [phys_col], "type": m_type}
#                         new_discoveries += 1
#                         continue
                
#                 clean_m = clean_physical_column(measure)
#                 resolved[measure] = {"formula": clean_m, "physical_columns": [clean_m], "type": m_type}
#                 new_discoveries += 1
#                 continue

#             base_cols = set()
#             queue = [curr_id]
#             visited = set()
            
#             while queue:
#                 cid = queue.pop(0)
#                 if cid in visited: continue
#                 visited.add(cid)

#                 cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_id = %s", (cid,))
#                 agg_row = cursor.fetchone()
#                 if not agg_row: continue

#                 fk = agg_row.get('measure_id', agg_row.get('MEASURE_ID'))
#                 sub_formula = agg_row.get('measure_formula', agg_row.get('MEASURE_FORMULA', ''))
                
#                 if fk and m_table:
#                     cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (fk,))
#                     m_row = cursor.fetchone()
#                     if m_row: base_cols.add(m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME')))
#                 else:
#                     children = []
#                     if mad_table:
#                         cursor.execute(f"SELECT source_measure_aggregation_id FROM {mad_table} WHERE target_measure_aggregation_id = %s", (cid,))
#                         children = [r.get('source_measure_aggregation_id', r.get('SOURCE_MEASURE_AGGREGATION_ID')) for r in cursor.fetchall()]
                    
#                     if children:
#                         queue.extend(children)
#                     else:
#                         extracted = extract_columns_from_formula(sub_formula)
#                         for c in extracted: base_cols.add(c)

#             clean_cols = [c.split('.')[-1] for c in base_cols if c]
#             resolved[measure] = {"formula": raw_formula, "physical_columns": clean_cols, "type": "FORMULA"}
#             new_discoveries += 1

#         if new_discoveries > 0:
#             cache.update(resolved)
#             save_cache(cache)
#             print(f"💾 Saved {new_discoveries} new definitions to run_logs/semantic_cache.json.")

#         dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
#         print("✅ Semantic Resolution complete.")
#         return {"resolved_semantics": resolved}

#     except Exception as e:
#         print(f"🔥 Semantic Resolver Error: {e}")
#         return {"resolved_semantics": resolved}
#     finally:
#         if 'cursor' in locals(): cursor.close()
#         if hasattr(conn, 'close'): conn.close()

# # --- 🗺️ NODE 3: DYNAMIC PATHFINDING ---
# # --- 🗺️ NODE 3: DYNAMIC PATHFINDING ---
# def load_join_cache():
#     if os.path.exists(JOIN_CACHE_FILE):
#         with open(JOIN_CACHE_FILE, "r", encoding="utf-8") as f:
#             try: return json.load(f)
#             except json.JSONDecodeError: return {}
#     return {}

# def save_join_cache(cache_data):
#     os.makedirs(os.path.dirname(JOIN_CACHE_FILE), exist_ok=True)
#     with open(JOIN_CACHE_FILE, "w", encoding="utf-8") as f:
#         json.dump(cache_data, f, indent=4)

# def node_pathfinder(state: PipelineState):
#     print("\n" + "="*60 + "\n🗺️ NODE 3: DYNAMIC PATHFINDING\n" + "="*60)
#     intent = state["extracted_intent"]
    
#     required_dims = set(intent.get("dimensions", []))
#     for f in intent.get("filter_columns", []): required_dims.add(f)
    
#     if not required_dims:
#         print("⏭️ No dimensions to join. Skipping Pathfinding.")
#         dump_log("03_join_paths.json", json.dumps({}, indent=2))
#         return {"join_paths": {}}

#     target_tables = intent.get("target_tables", [])
#     primary_fact = target_tables[0] if target_tables else "fact_data"
    
#     cache = load_join_cache()
#     join_paths = {}
#     missing_dims = []

#     for dim in required_dims:
#         cache_key = f"{primary_fact}_to_{dim}"
#         if cache_key in cache: join_paths[dim] = cache[cache_key]
#         else: missing_dims.append(dim)

#     if not missing_dims:
#         print("⚡ 100% Join Cache Hit!")
#         dump_log("03_join_paths.json", json.dumps(join_paths, indent=2))
#         return {"join_paths": join_paths}

#     print(f"🔍 Cache Miss for {len(missing_dims)} dimensions. Introspecting database schema...")
    
#     conn = get_db_connection()
#     if isinstance(conn, str): return {"join_paths": join_paths}
    
#     try:
#         cursor = get_dict_cursor(conn)
        
#         # Get Fact Table Columns
#         cursor.execute(f"DESCRIBE {primary_fact}")
#         fact_cols = [r['Field'].lower() for r in cursor.fetchall()]
        
#         new_discoveries = 0
#         for dim in missing_dims:
#             cursor.execute(f"SHOW TABLES LIKE '%{dim}%'")
#             possible_tables = [list(r.values())[0] for r in cursor.fetchall()]
            
#             dim_table = None
#             xref_table = None
            
#             for pt in possible_tables:
#                 if 'xref' in pt: xref_table = pt
#                 elif 'desc' in pt or 'dim' in pt: dim_table = pt
            
#             if not dim_table and possible_tables: dim_table = possible_tables[0]
#             if not dim_table:
#                 print(f"⚠️ Could not locate any dimension table containing '{dim}'.")
#                 continue

#             print(f"✅ Discovered Dimension Table: {dim_table}")
            
#             # 🚀 THE FIX: DYNAMIC COLUMN INTERSECTION
#             cursor.execute(f"DESCRIBE {dim_table}")
#             dim_cols = [r['Field'].lower() for r in cursor.fetchall()]
            
#             # Find the shared key between Fact and Dim
#             shared_keys = list(set(fact_cols) & set(dim_cols))
#             direct_join_col = next((c for c in shared_keys if c.endswith('_id')), None)
            
#             path = []
            
#             if direct_join_col:
#                 print(f"   🔗 Direct Join Found using shared key: {direct_join_col}")
#                 path = [{"table": dim_table, "on": f"{primary_fact}.{direct_join_col} = {dim_table}.{direct_join_col}"}]
                
#             elif xref_table:
#                 print(f"🌉 Discovered Bridge Table: {xref_table}")
#                 cursor.execute(f"DESCRIBE {xref_table}")
#                 xref_cols = [r['Field'].lower() for r in cursor.fetchall()]
                
#                 # 1. Fact to XREF overlap
#                 fact_to_xref_overlap = list(set(fact_cols) & set(xref_cols))
#                 bridge_col_1 = next((c for c in fact_to_xref_overlap if c.endswith('_id')), None)
                
#                 # 2. XREF to Dim overlap
#                 xref_to_dim_overlap = list(set(xref_cols) & set(dim_cols))
#                 bridge_col_2 = next((c for c in xref_to_dim_overlap if c.endswith('_id')), None)
                
#                 if bridge_col_1 and bridge_col_2:
#                     path = [
#                         {"table": xref_table, "on": f"{primary_fact}.{bridge_col_1} = {xref_table}.{bridge_col_1}"},
#                         {"table": dim_table, "on": f"{xref_table}.{bridge_col_2} = {dim_table}.{bridge_col_2}"}
#                     ]
            
#             if path:
#                 cache_key = f"{primary_fact}_to_{dim}"
#                 join_paths[dim] = path
#                 cache[cache_key] = path
#                 new_discoveries += 1
#             else:
#                 print(f"❌ Failed to find a valid join path between {primary_fact} and {dim_table}.")

#         if new_discoveries > 0:
#             save_join_cache(cache)
#             print(f"💾 Saved {new_discoveries} new Join Paths to run_logs/join_cache.json.")

#         dump_log("03_join_paths.json", json.dumps(join_paths, indent=2))
#         print("✅ Pathfinding complete.")
#         return {"join_paths": join_paths}

#     finally:
#         cursor.close()
#         conn.close()

# # --- 🕸️ BUILD GRAPH ---
# def build_pipeline():
#     workflow = StateGraph(PipelineState)
#     workflow.add_node("Extract_Intent", node_extract_intent)
#     workflow.add_node("Semantic_Resolver", node_semantic_resolver)
#     workflow.add_node("Pathfinder", node_pathfinder)
    
#     workflow.set_entry_point("Extract_Intent")
#     workflow.add_edge("Extract_Intent", "Semantic_Resolver")
#     workflow.add_edge("Semantic_Resolver", "Pathfinder")
#     workflow.add_edge("Pathfinder", END) 
    
#     return workflow.compile()

# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument('--api-file', required=True)
#     args = parser.parse_args()
    
#     print("Initializing Orchestrator (Nodes 1, 2, 3)...")
#     with open(args.api_file, 'r', encoding='utf-8') as f: 
#         raw_payload = json.load(f)
        
#     app = build_pipeline()
#     initial_state = {
#         "api_payload": raw_payload, 
#         "extracted_intent": {}, 
#         "resolved_semantics": {},
#         "join_paths": {},
#         "error_message": ""
#     }
    
#     result = app.invoke(initial_state)
#     print("\n✅ Execution complete. Check run_logs/ for Node 1, 2, and 3 outputs.")













import os
import json
import argparse
import re
import sqlglot
import sqlglot.expressions as exp
from typing import TypedDict, Dict, Any

from langgraph.graph import StateGraph, END
from dotenv import load_dotenv
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

load_dotenv()

# --- 📝 DYNAMIC HOST-SPECIFIC OBSERVABILITY SETUP ---
def get_host_specific_log_dir():
    host = os.getenv("DB_HOST", "unknown_host").replace(".", "_")
    db = os.getenv("DB_DATABASE")
    if not db: db = os.getenv("DB_SCHEMA", "unknown_db")
    return os.path.join("run_logs", f"{host}_{db}")

LOG_DIR = get_host_specific_log_dir()
os.makedirs(LOG_DIR, exist_ok=True)

CACHE_FILE = os.path.join(LOG_DIR, "semantic_cache.json")
JOIN_CACHE_FILE = os.path.join(LOG_DIR, "join_cache.json")
FILTER_CACHE_FILE = os.path.join(LOG_DIR, "filter_cache.json")

def dump_log(filename: str, content: str):
    filepath = os.path.join(LOG_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

def clean_physical_column(col):
    if not col or str(col).strip() == "*": return "*"
    c = str(col).split('.')[-1].lower().strip()
    if c.startswith("sum_"): return c[4:]
    return c

# --- GRAPH STATE ---
class PipelineState(TypedDict):
    api_payload: Dict[str, Any]
    extracted_intent: Dict[str, Any]
    resolved_semantics: Dict[str, Any]
    join_paths: Dict[str, Any]
    resolved_filters: Dict[str, str] # 🚀 NEW: Holds the verified physical column names for filters
    error_message: str

# --- 🚀 NODE 1: INTENT EXTRACTION ---
def node_extract_intent(state: PipelineState):
    print("\n" + "="*60 + "\n🚀 NODE 1: EXTRACT INTENT\n" + "="*60)
    payload = state["api_payload"]
    dump_log("00_raw_api_payload.json", json.dumps(payload, indent=2))
    
    variables = payload.get("variables", payload)
    query_block = variables.get("query")
    if not isinstance(query_block, dict): query_block = {}
    
    def ensure_list(item):
        if not item: return []
        if isinstance(item, list): return item
        return [item]
        
    dim_levels = ensure_list(query_block.get("dimensionLevels"))
    agg_measures = ensure_list(query_block.get("aggregatedMeasures"))
    datatables = ensure_list(query_block.get("datatable") or variables.get("datatable"))
    
    filter_cols, raw_filters = [], []
    filters = query_block.get("scope", {})
    if filters and "dimensionFilters" in filters:
        for f in ensure_list(filters.get("dimensionFilters")):
            raw_filters.append(f)
            if isinstance(f, dict) and "dimensionColumnName" in f: filter_cols.append(f["dimensionColumnName"])
            for cond in ensure_list(f.get("and", [])):
                if isinstance(cond, dict) and "dimensionLevelColumnName" in cond: filter_cols.append(cond["dimensionLevelColumnName"])
                    
    direct_cols = ensure_list(variables.get("columnsToFetch"))
    for df in ensure_list(variables.get("filters")):
        if isinstance(df, dict) and "columnName" in df: 
            filter_cols.append(df["columnName"])
            raw_filters.append({
                "dimensionColumnName": df["columnName"],
                "and": [{"dimensionLevelColumnName": df["columnName"], "cmpOperator": df.get("operator", "EQ"), "values": df.get("value", [])}]
            })
                    
    sort_cols = []
    seen_sorts = set()
    for s in ensure_list((query_block.get("sort") or {}).get("entries", [])):
        if isinstance(s, dict) and "columnName" in s:
            col = s["columnName"]
            if col not in seen_sorts:
                sort_cols.append({"columnName": col, "direction": s.get("direction", "ASC").upper()})
                seen_sorts.add(col)
        
    limit_rows = query_block.get("first") or variables.get("first") or 200
    is_direct_fetch = len(direct_cols) > 0
    is_dim_member_fetch = len(dim_levels) > 0 and len(agg_measures) == 0

    intent = {
        "is_direct_fetch": is_direct_fetch,
        "is_dim_member_fetch": is_dim_member_fetch,
        "target_tables": datatables, 
        "dimensions": list(set(dim_levels)),
        "measures": list(set(agg_measures)), 
        "filter_columns": list(set(filter_cols)),
        "sort_columns": sort_cols, 
        "direct_columns": list(set(direct_cols)),
        "raw_filters": raw_filters,
        "limit": limit_rows
    }
    
    dump_log("01_extracted_intent.json", json.dumps(intent, indent=2))
    print("✅ Intent Extracted.")
    return {"extracted_intent": intent, "error_message": ""}

# --- 🧠 NODE 2: SEMANTIC RESOLVER ---
def load_json_cache(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except json.JSONDecodeError: return {}
    return {}

def save_json_cache(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def extract_columns_from_formula(formula_string):
    if not formula_string: return []
    try:
        tree = sqlglot.parse_one(formula_string)
        return list(set([node.name for node in tree.find_all(exp.Column)]))
    except Exception: return []

def node_semantic_resolver(state: PipelineState):
    print("\n" + "="*60 + "\n🧠 NODE 2: SEMANTIC RESOLVER\n" + "="*60)
    intent = state["extracted_intent"]
    requested_measures = intent.get("measures", [])
    
    if not requested_measures:
        print("⏭️ No measures to resolve. Skipping Semantic Graph traversal.")
        dump_log("02_resolved_semantics.json", json.dumps({}, indent=2))
        return {"resolved_semantics": {}}

    cache = load_json_cache(CACHE_FILE)
    resolved = {}
    missing_measures = []

    for m in requested_measures:
        if m in cache: resolved[m] = cache[m]
        else: missing_measures.append(m)

    if not missing_measures:
        print("⚡ 100% Cache Hit!")
        dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
        return {"resolved_semantics": resolved}
    
    conn = get_db_connection()
    if isinstance(conn, str): return {"resolved_semantics": resolved}
    
    try:
        cursor = get_dict_cursor(conn)
        
        tables_to_check = ['measures', 'measure_aggregations', 'measure_aggregation_dependencies']
        found_tables = {}
        for t in tables_to_check:
            cursor.execute(f"SHOW TABLES LIKE '%{t}%'")
            res = cursor.fetchall()
            if res: found_tables[t] = list(res[0].values())[0]

        ma_table = found_tables.get('measure_aggregations')
        mad_table = found_tables.get('measure_aggregation_dependencies')
        m_table = found_tables.get('measures')

        if not ma_table:
            for m in missing_measures: 
                clean_m = clean_physical_column(m)
                resolved[m] = {"formula": clean_m, "physical_columns": [clean_m], "type": "SUM"}
            cache.update(resolved)
            save_json_cache(CACHE_FILE, cache)
            dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
            return {"resolved_semantics": resolved}

        new_discoveries = 0
        for measure in missing_measures:
            if measure.lower() == 'row_count':
                resolved[measure] = {"formula": "COUNT(*)", "physical_columns": ["*"], "type": "COUNT"}
                new_discoveries += 1
                continue
            if measure.lower() in ['is_editable', 'pt_editable', 'imputed_flag', 'planner_tag']:
                resolved[measure] = {"formula": "NULL", "physical_columns": [], "type": "FORMULA"}
                new_discoveries += 1
                continue

            cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_column_name = %s LIMIT 1", (measure,))
            row = cursor.fetchone()
            
            if not row:
                clean_m = clean_physical_column(measure)
                resolved[measure] = {"formula": clean_m, "physical_columns": [clean_m], "type": "SUM"}
                new_discoveries += 1
                continue

            curr_id = row.get('measure_aggregation_id', row.get('MEASURE_AGGREGATION_ID'))
            raw_formula = row.get('measure_formula', row.get('MEASURE_FORMULA', ''))
            m_type = str(row.get('measure_aggregation_type', row.get('MEASURE_AGGREGATION_TYPE', 'SUM'))).upper()

            if "FORMULA" not in m_type:
                m_fk = row.get('measure_id', row.get('MEASURE_ID'))
                if m_fk and m_table:
                    cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (m_fk,))
                    m_row = cursor.fetchone()
                    if m_row:
                        phys_col = m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME'))
                        resolved[measure] = {"formula": phys_col, "physical_columns": [phys_col], "type": m_type}
                        new_discoveries += 1
                        continue
                
                clean_m = clean_physical_column(measure)
                resolved[measure] = {"formula": clean_m, "physical_columns": [clean_m], "type": m_type}
                new_discoveries += 1
                continue

            base_cols = set()
            queue = [curr_id]
            visited = set()
            
            while queue:
                cid = queue.pop(0)
                if cid in visited: continue
                visited.add(cid)

                cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_id = %s", (cid,))
                agg_row = cursor.fetchone()
                if not agg_row: continue

                fk = agg_row.get('measure_id', agg_row.get('MEASURE_ID'))
                sub_formula = agg_row.get('measure_formula', agg_row.get('MEASURE_FORMULA', ''))
                
                if fk and m_table:
                    cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (fk,))
                    m_row = cursor.fetchone()
                    if m_row: base_cols.add(m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME')))
                else:
                    children = []
                    if mad_table:
                        cursor.execute(f"SELECT source_measure_aggregation_id FROM {mad_table} WHERE target_measure_aggregation_id = %s", (cid,))
                        children = [r.get('source_measure_aggregation_id', r.get('SOURCE_MEASURE_AGGREGATION_ID')) for r in cursor.fetchall()]
                    
                    if children:
                        queue.extend(children)
                    else:
                        extracted = extract_columns_from_formula(sub_formula)
                        for c in extracted: base_cols.add(c)

            clean_cols = [c.split('.')[-1] for c in base_cols if c]
            resolved[measure] = {"formula": raw_formula, "physical_columns": clean_cols, "type": "FORMULA"}
            new_discoveries += 1

        if new_discoveries > 0:
            cache.update(resolved)
            save_json_cache(CACHE_FILE, cache)

        dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
        print("✅ Semantic Resolution complete.")
        return {"resolved_semantics": resolved}

    finally:
        if 'cursor' in locals(): cursor.close()
        if hasattr(conn, 'close'): conn.close()

# --- 🗺️ NODE 3: DYNAMIC PATHFINDING & FILTER RESOLUTION ---
def build_schema_graph(cursor, schema_name):
    cursor.execute("""
        SELECT TABLE_NAME, COLUMN_NAME 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_SCHEMA = %s
    """, (schema_name,))
    
    table_to_cols = {}
    col_to_tables = {}
    
    for r in cursor.fetchall():
        t = r.get('TABLE_NAME', r.get('table_name')).lower()
        c = r.get('COLUMN_NAME', r.get('column_name')).lower()
        
        if t not in table_to_cols: table_to_cols[t] = set()
        table_to_cols[t].add(c)
        
        if c.endswith('_id'):
            if c not in col_to_tables: col_to_tables[c] = set()
            col_to_tables[c].add(t)
            
    return table_to_cols, col_to_tables

def validate_path_data(cursor, path):
    try:
        for step in path:
            table1 = step['on'].split(' = ')[0].split('.')[0]
            col = step['on'].split(' = ')[0].split('.')[1]
            table2 = step['table']
            
            cursor.execute(f"SELECT {col} FROM {table2} WHERE {col} IS NOT NULL LIMIT 1")
            res = cursor.fetchall() # 🚀 FIX: Clear buffer
            if not res: continue 
            
            sample_val = list(res[0].values())[0]
            cursor.execute(f"SELECT 1 FROM {table1} WHERE {col} = %s LIMIT 1", (sample_val,))
            if not cursor.fetchall(): return False # 🚀 FIX: Clear buffer
        return True
    except Exception: 
        try: cursor.fetchall()
        except: pass
        return True 

def find_shortest_join_path(start_table, end_table, table_to_cols, col_to_tables):
    if start_table == end_table: return []
    
    queue = [(start_table, [])]
    visited = set([start_table])
    
    while queue:
        current_table, path = queue.pop(0)
        
        for col in table_to_cols.get(current_table, []):
            if col.endswith('_id') and col in col_to_tables:
                
                # Prioritize canonical tables over decoys
                neighbors = sorted(list(col_to_tables[col]), key=lambda x: (
                    0 if x.endswith('_xref') else 1,
                    0 if x.endswith('_desc') else 1,
                    len(x) 
                ))
                
                for neighbor_table in neighbors:
                    if neighbor_table not in visited:
                        new_path = list(path)
                        new_path.append({
                            "table": neighbor_table,
                            "on": f"{current_table}.{col} = {neighbor_table}.{col}"
                        })
                        
                        if neighbor_table == end_table: return new_path
                            
                        visited.add(neighbor_table)
                        queue.append((neighbor_table, new_path))
                        
    return None

# 🚀 NEW: EMPIRICAL FILTER RESOLUTION 🚀
def resolve_filter_column(cursor, dim_table, filter_col, sample_values):
    """
    Finds the exact physical column in the table that holds the filter value.
    If the UI passes "location" and [60007302], it tests columns until it finds one where 60007302 exists.
    """
    if not sample_values: return filter_col
    sample_val = sample_values[0]
    
    # 1. Try the exact name first
    try:
        cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {filter_col} = %s LIMIT 1", (sample_val,))
        if cursor.fetchall(): return filter_col # 🚀 FIX: Check and clear buffer
    except Exception: 
        try: cursor.fetchall()
        except: pass
    
    # 2. Try adding _id
    try:
        test_col = f"{filter_col}_id"
        cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {test_col} = %s LIMIT 1", (sample_val,))
        if cursor.fetchall(): return test_col # 🚀 FIX: Check and clear buffer
    except Exception: 
        try: cursor.fetchall()
        except: pass
    
    # 3. Query the table schema and test EVERY column until data matches
    try:
        cursor.execute(f"DESCRIBE {dim_table}")
        all_cols = [r['Field'].lower() for r in cursor.fetchall()]
        
        for col in all_cols:
            try:
                cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {col} = %s LIMIT 1", (sample_val,))
                if cursor.fetchall():
                    return col # We proved the data lives here!
            except Exception: 
                try: cursor.fetchall()
                except: pass
    except Exception: pass
    
    return filter_col # Fallback to original string if all tests fail


def node_pathfinder(state: PipelineState):
    print("\n" + "="*60 + "\n🗺️ NODE 3: DYNAMIC PATHFINDING & FILTER RESOLUTION\n" + "="*60)
    intent = state["extracted_intent"]
    
    required_dims = set(intent.get("dimensions", []))
    for f in intent.get("filter_columns", []): required_dims.add(f)
    
    target_tables = intent.get("target_tables", [])
    primary_fact = target_tables[0] if target_tables else "fact_data"
    
    join_cache = load_json_cache(JOIN_CACHE_FILE)
    filter_cache = load_json_cache(FILTER_CACHE_FILE)
    
    join_paths = {}
    resolved_filters = {}
    missing_dims = []
    missing_filters = []

    # Check join cache
    for dim in required_dims:
        cache_key = f"{primary_fact}_to_{dim}"
        if cache_key in join_cache: join_paths[dim] = join_cache[cache_key]
        else: missing_dims.append(dim)
            
    # Check filter cache
    for raw_f in intent.get("raw_filters", []):
        dim_col = raw_f.get("dimensionColumnName")
        if not dim_col: continue
        
        for cond in raw_f.get("and", []):
            level_col = cond.get("dimensionLevelColumnName")
            cache_key = f"{dim_col}_{level_col}"
            if cache_key in filter_cache:
                resolved_filters[level_col] = filter_cache[cache_key]
            else:
                missing_filters.append(raw_f)

    if not missing_dims and not missing_filters:
        print("⚡ 100% Cache Hit!")
        dump_log("03_join_paths.json", json.dumps(join_paths, indent=2))
        dump_log("04_resolved_filters.json", json.dumps(resolved_filters, indent=2))
        return {"join_paths": join_paths, "resolved_filters": resolved_filters}

    conn = get_db_connection()
    if isinstance(conn, str): return {"join_paths": join_paths, "resolved_filters": resolved_filters}
    
    try:
        db_type = get_db_type()
        cursor = get_dict_cursor(conn)
        
        schema_name = os.getenv("DB_SCHEMA", "public") if db_type == "postgres" else os.getenv("DB_DATABASE")
        if not schema_name and db_type != "postgres":
            cursor.execute("SELECT DATABASE() as db")
            res = cursor.fetchone()
            schema_name = res.get('db', res.get('DB')) if res else None

        table_to_cols, col_to_tables = build_schema_graph(cursor, schema_name)
        
        new_joins, new_filters = 0, 0
        dim_to_table_map = {} # Store mapped tables for filter resolution
        
        # 1. Resolve Joins
        for dim in missing_dims:
            base_dim = re.sub(r'\d+$', '', dim).strip('_')
            cursor.execute(f"SHOW TABLES LIKE '%{dim}%'")
            possible_tables = [list(r.values())[0].lower() for r in cursor.fetchall()]
            
            dim_table = None
            if f"{dim}_dim_desc" in possible_tables: dim_table = f"{dim}_dim_desc"
            elif f"{base_dim}_dim_desc" in possible_tables: dim_table = f"{base_dim}_dim_desc"
            
            if not dim_table:
                starts_with = [t for t in possible_tables if t.startswith(f"{dim}_") and ('desc' in t or 'dim' in t)]
                if not starts_with and base_dim != dim:
                    starts_with = [t for t in possible_tables if t.startswith(f"{base_dim}_") and ('desc' in t or 'dim' in t)]
                if starts_with: dim_table = starts_with[0]
                
            if not dim_table:
                contains = [t for t in possible_tables if ('desc' in t or 'dim' in t)]
                if contains: dim_table = contains[0]
                
            if not dim_table and possible_tables: dim_table = possible_tables[0]
            if not dim_table: continue
            
            dim_to_table_map[dim] = dim_table # Save for filter step
                
            path = find_shortest_join_path(primary_fact, dim_table, table_to_cols, col_to_tables)
            
            if path and validate_path_data(cursor, path):
                cache_key = f"{primary_fact}_to_{dim}"
                join_paths[dim] = path
                join_cache[cache_key] = path
                new_joins += 1

        # 2. Resolve Filters
        for raw_f in missing_filters:
            dim_col = raw_f.get("dimensionColumnName")
            parent_dim_table = dim_to_table_map.get(dim_col)
            if not parent_dim_table: 
                path = join_paths.get(dim_col, [])
                parent_dim_table = path[-1].get("table") if path else primary_fact
            
            for cond in raw_f.get("and", []):
                level_col = cond.get("dimensionLevelColumnName")
                values = cond.get("values", [])
                
                # 🚀 THE FIX: Prioritize the Level Table over the Parent Table
                dim_table = dim_to_table_map.get(level_col)
                if not dim_table:
                    level_path = join_paths.get(level_col, [])
                    if level_path: dim_table = level_path[-1].get("table")
                    else: dim_table = parent_dim_table # Fallback to parent
                
                print(f"🔎 Testing Filter: [{level_col}] in table [{dim_table}] with value {values}...")
                true_col = resolve_filter_column(cursor, dim_table, level_col, values)
                print(f"   ✅ Data Verified! Correct physical column is: {true_col}")
                
                cache_key = f"{dim_col}_{level_col}"
                resolved_filters[level_col] = f"{dim_table}.{true_col}" # Append table prefix for strict SQL mapping
                filter_cache[cache_key] = f"{dim_table}.{true_col}"
                new_filters += 1

        if new_joins > 0: save_json_cache(JOIN_CACHE_FILE, join_cache)
        if new_filters > 0: save_json_cache(FILTER_CACHE_FILE, filter_cache)

        dump_log("03_join_paths.json", json.dumps(join_paths, indent=2))
        dump_log("04_resolved_filters.json", json.dumps(resolved_filters, indent=2))
        print("✅ Pathfinding and Filter Resolution complete.")
        return {"join_paths": join_paths, "resolved_filters": resolved_filters}

    finally:
        cursor.close()
        conn.close()

# --- 🕸️ BUILD GRAPH ---
def build_pipeline():
    workflow = StateGraph(PipelineState)
    workflow.add_node("Extract_Intent", node_extract_intent)
    workflow.add_node("Semantic_Resolver", node_semantic_resolver)
    workflow.add_node("Pathfinder", node_pathfinder)
    
    workflow.set_entry_point("Extract_Intent")
    workflow.add_edge("Extract_Intent", "Semantic_Resolver")
    workflow.add_edge("Semantic_Resolver", "Pathfinder")
    workflow.add_edge("Pathfinder", END) 
    
    return workflow.compile()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-file', required=True)
    args = parser.parse_args()
    
    print("Initializing Orchestrator (Nodes 1, 2, 3)...")
    with open(args.api_file, 'r', encoding='utf-8') as f: 
        raw_payload = json.load(f)
        
    app = build_pipeline()
    initial_state = {
        "api_payload": raw_payload, 
        "extracted_intent": {}, 
        "resolved_semantics": {},
        "join_paths": {},
        "resolved_filters": {},
        "error_message": ""
    }
    
    result = app.invoke(initial_state)
    print("\n✅ Execution complete. Check isolated run_logs/ for your specific Host DB.")
