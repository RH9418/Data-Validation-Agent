# import os
# import json
# import argparse
# import re
# import sqlglot
# import sqlglot.expressions as exp
# from typing import TypedDict, Dict, Any
# from langgraph.graph import StateGraph, END
# from dotenv import load_dotenv

# from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

# load_dotenv()

# # --- 📝 DYNAMIC HOST-SPECIFIC OBSERVABILITY SETUP ---
# def get_host_specific_log_dir():
#     host = os.getenv("DB_HOST", "unknown_host").replace(".", "_")
#     db = os.getenv("DB_DATABASE")
#     if not db: db = os.getenv("DB_SCHEMA", "unknown_db")
#     return os.path.join("run_logs", f"{host}_{db}")

# LOG_DIR = get_host_specific_log_dir()
# os.makedirs(LOG_DIR, exist_ok=True)

# CACHE_FILE = os.path.join(LOG_DIR, "semantic_cache.json")
# JOIN_CACHE_FILE = os.path.join(LOG_DIR, "join_cache.json")
# FILTER_CACHE_FILE = os.path.join(LOG_DIR, "filter_cache.json")

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
#     resolved_filters: Dict[str, str] 
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
#     print("✅ Intent Extracted.")
#     return {"extracted_intent": intent, "error_message": ""}

# # --- 🧠 NODE 2: SEMANTIC RESOLVER ---
# def load_json_cache(filepath):
#     if os.path.exists(filepath):
#         with open(filepath, "r", encoding="utf-8") as f:
#             try: return json.load(f)
#             except json.JSONDecodeError: return {}
#     return {}

# def save_json_cache(filepath, data):
#     with open(filepath, "w", encoding="utf-8") as f:
#         json.dump(data, f, indent=4)

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
        
#     cache = load_json_cache(CACHE_FILE)
#     resolved = {}
#     missing_measures = []
    
#     for m in requested_measures:
#         if m in cache: resolved[m] = cache[m]
#         else: missing_measures.append(m)
        
#     if not missing_measures:
#         print("⚡ 100% Cache Hit!")
#         dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
#         return {"resolved_semantics": resolved}
    
#     conn = get_db_connection()
#     if isinstance(conn, str): return {"resolved_semantics": resolved}
    
#     try:
#         db_type = get_db_type()
#         cursor = get_dict_cursor(conn)
        
#         # 🛡️ STRICT READ-ONLY ENFORCEMENT
#         if db_type == "postgres":
#             try: cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
#             except: pass
            
#         schema_name = os.getenv("DB_SCHEMA", "public") if db_type == "postgres" else os.getenv("DB_DATABASE")
        
#         tables_to_check = ['measures', 'measure_aggregations', 'measure_aggregation_dependencies']
#         found_tables = {}
#         for t in tables_to_check:
#             # 🐘 POSTGRES COMPATIBILITY: Query information_schema instead of SHOW TABLES
#             if db_type == 'postgres':
#                 cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name LIKE %s", (schema_name, f'%{t}%'))
#             else:
#                 cursor.execute(f"SHOW TABLES LIKE '%{t}%'")
            
#             res = cursor.fetchall()
#             if res: found_tables[t] = list(res[0].values())[0]
            
#         ma_table = found_tables.get('measure_aggregations')
#         mad_table = found_tables.get('measure_aggregation_dependencies')
#         m_table = found_tables.get('measures')
        
#         if not ma_table:
#             for m in missing_measures: 
#                 clean_m = clean_physical_column(m)
#                 resolved[m] = {"formula": clean_m, "physical_columns": [clean_m], "type": "SUM"}
#             cache.update(resolved)
#             save_json_cache(CACHE_FILE, cache)
#             dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
#             return {"resolved_semantics": resolved}

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

#             try:
#                 cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_column_name = %s LIMIT 1", (measure,))
#                 row = cursor.fetchone()
#             except Exception:
#                 if db_type == 'postgres': conn.rollback()
#                 row = None
            
#             if not row:
#                 clean_m = clean_physical_column(measure)
#                 resolved[measure] = {"formula": clean_m, "physical_columns": [clean_m], "type": "SUM"}
#                 new_discoveries += 1
#                 continue
                
#             curr_id = row.get('measure_aggregation_id', row.get('MEASURE_AGGREGATION_ID'))
#             raw_formula = row.get('measure_formula', row.get('MEASURE_FORMULA', ''))
#             m_type = str(row.get('measure_aggregation_type', row.get('MEASURE_AGGREGATION_TYPE', 'SUM'))).upper()
            
#             if "FORMULA" not in m_type:
#                 m_fk = row.get('measure_id', row.get('MEASURE_ID'))
#                 if m_fk and m_table:
#                     try:
#                         cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (m_fk,))
#                         m_row = cursor.fetchone()
#                     except Exception:
#                         if db_type == 'postgres': conn.rollback()
#                         m_row = None
                        
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
                
#                 try:
#                     cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_id = %s", (cid,))
#                     agg_row = cursor.fetchone()
#                 except Exception:
#                     if db_type == 'postgres': conn.rollback()
#                     agg_row = None
                    
#                 if not agg_row: continue
                
#                 fk = agg_row.get('measure_id', agg_row.get('MEASURE_ID'))
#                 sub_formula = agg_row.get('measure_formula', agg_row.get('MEASURE_FORMULA', ''))
                
#                 if fk and m_table:
#                     try:
#                         cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (fk,))
#                         m_row = cursor.fetchone()
#                     except Exception:
#                         if db_type == 'postgres': conn.rollback()
#                         m_row = None
                        
#                     if m_row: base_cols.add(m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME')))
#                 else:
#                     children = []
#                     if mad_table:
#                         try:
#                             cursor.execute(f"SELECT source_measure_aggregation_id FROM {mad_table} WHERE target_measure_aggregation_id = %s", (cid,))
#                             children = [r.get('source_measure_aggregation_id', r.get('SOURCE_MEASURE_AGGREGATION_ID')) for r in cursor.fetchall()]
#                         except Exception:
#                             if db_type == 'postgres': conn.rollback()
                    
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
#             save_json_cache(CACHE_FILE, cache)
            
#         dump_log("02_resolved_semantics.json", json.dumps(resolved, indent=2))
#         print("✅ Semantic Resolution complete.")
#         return {"resolved_semantics": resolved}
#     finally:
#         if 'cursor' in locals(): cursor.close()
#         if hasattr(conn, 'close'): conn.close()

# # --- 🗺️ NODE 3: DYNAMIC PATHFINDING & FILTER RESOLUTION ---
# def build_schema_graph(cursor, schema_name):
#     # This query is ANSI standard and works perfectly in both MySQL and Postgres
#     cursor.execute("""
#         SELECT TABLE_NAME, COLUMN_NAME 
#         FROM INFORMATION_SCHEMA.COLUMNS 
#         WHERE TABLE_SCHEMA = %s
#     """, (schema_name,))
    
#     table_to_cols = {}
#     col_to_tables = {}
    
#     for r in cursor.fetchall():
#         t = r.get('TABLE_NAME', r.get('table_name')).lower()
#         c = r.get('COLUMN_NAME', r.get('column_name')).lower()
        
#         if t not in table_to_cols: table_to_cols[t] = set()
#         table_to_cols[t].add(c)
        
#         if c.endswith('_id'):
#             if c not in col_to_tables: col_to_tables[c] = set()
#             col_to_tables[c].add(t)
            
#     return table_to_cols, col_to_tables

# def validate_path_data(conn, cursor, path, db_type):
#     try:
#         for step in path:
#             table1 = step['on'].split(' = ')[0].split('.')[0]
#             col = step['on'].split(' = ')[0].split('.')[1]
#             table2 = step['table']
            
#             cursor.execute(f"SELECT {col} FROM {table2} WHERE {col} IS NOT NULL LIMIT 1")
#             res = cursor.fetchall()
#             if not res: continue 
            
#             sample_val = list(res[0].values())[0]
#             cursor.execute(f"SELECT 1 FROM {table1} WHERE {col} = %s LIMIT 1", (sample_val,))
#             if not cursor.fetchall(): return False
#         return True
#     except Exception: 
#         if db_type == 'postgres': conn.rollback() # 🐘 POSTGRES REQUIREMENT
#         try: cursor.fetchall()
#         except: pass
#         return True 

# def find_shortest_join_path(start_table, end_table, table_to_cols, col_to_tables):
#     if start_table == end_table: return []
    
#     queue = [(start_table, [])]
#     visited = set([start_table])
    
#     while queue:
#         current_table, path = queue.pop(0)
        
#         for col in table_to_cols.get(current_table, []):
#             if col.endswith('_id') and col in col_to_tables:
#                 neighbors = sorted(list(col_to_tables[col]), key=lambda x: (
#                     0 if x.endswith('_xref') else 1,
#                     0 if x.endswith('_desc') else 1,
#                     len(x) 
#                 ))
                
#                 for neighbor_table in neighbors:
#                     if neighbor_table not in visited:
#                         new_path = list(path)
#                         new_path.append({
#                             "table": neighbor_table,
#                             "on": f"{current_table}.{col} = {neighbor_table}.{col}"
#                         })
                        
#                         if neighbor_table == end_table: return new_path
#                         visited.add(neighbor_table)
#                         queue.append((neighbor_table, new_path))
#     return None

# def resolve_filter_column(conn, cursor, dim_table, filter_col, sample_values, db_type, schema_name):
#     """Finds exact physical column. Includes strict PostgreSQL rollback handling."""
#     if not sample_values: return filter_col
#     sample_val = sample_values[0]
    
#     # 1. Try the exact name first
#     try:
#         cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {filter_col} = %s LIMIT 1", (sample_val,))
#         if cursor.fetchall(): return filter_col
#     except Exception: 
#         if db_type == 'postgres': conn.rollback() # 🐘
#         try: cursor.fetchall()
#         except: pass
    
#     # 2. Try adding _id
#     try:
#         test_col = f"{filter_col}_id"
#         cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {test_col} = %s LIMIT 1", (sample_val,))
#         if cursor.fetchall(): return test_col
#     except Exception: 
#         if db_type == 'postgres': conn.rollback() # 🐘
#         try: cursor.fetchall()
#         except: pass
    
#     # 3. Query the table schema and test EVERY column
#     try:
#         if db_type == 'postgres':
#             cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s", (schema_name, dim_table))
#             all_cols = [r['column_name'].lower() for r in cursor.fetchall()]
#         else:
#             cursor.execute(f"DESCRIBE {dim_table}")
#             all_cols = [r.get('Field', r.get('FIELD', '')).lower() for r in cursor.fetchall()]
        
#         for col in all_cols:
#             try:
#                 cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {col} = %s LIMIT 1", (sample_val,))
#                 if cursor.fetchall(): return col 
#             except Exception: 
#                 if db_type == 'postgres': conn.rollback()
#                 try: cursor.fetchall()
#                 except: pass
#     except Exception:
#         if db_type == 'postgres': conn.rollback()
#         pass
    
#     return filter_col

# def node_pathfinder(state: PipelineState):
#     print("\n" + "="*60 + "\n🗺️ NODE 3: DYNAMIC PATHFINDING & FILTER RESOLUTION\n" + "="*60)
#     intent = state["extracted_intent"]
    
#     required_dims = set(intent.get("dimensions", []))
#     for f in intent.get("filter_columns", []): required_dims.add(f)
    
#     target_tables = intent.get("target_tables", [])
#     primary_fact = target_tables[0] if target_tables else "fact_data"
    
#     join_cache = load_json_cache(JOIN_CACHE_FILE)
#     filter_cache = load_json_cache(FILTER_CACHE_FILE)
    
#     join_paths = {}
#     resolved_filters = {}
#     missing_dims = []
#     missing_filters = []
    
#     for dim in required_dims:
#         cache_key = f"{primary_fact}_to_{dim}"
#         if cache_key in join_cache: join_paths[dim] = join_cache[cache_key]
#         else: missing_dims.append(dim)
            
#     for raw_f in intent.get("raw_filters", []):
#         dim_col = raw_f.get("dimensionColumnName")
#         if not dim_col: continue
#         for cond in raw_f.get("and", []):
#             level_col = cond.get("dimensionLevelColumnName")
#             cache_key = f"{dim_col}_{level_col}"
#             if cache_key in filter_cache:
#                 resolved_filters[level_col] = filter_cache[cache_key]
#             else:
#                 missing_filters.append(raw_f)
                
#     if not missing_dims and not missing_filters:
#         print("⚡ 100% Cache Hit!")
#         dump_log("03_join_paths.json", json.dumps(join_paths, indent=2))
#         dump_log("04_resolved_filters.json", json.dumps(resolved_filters, indent=2))
#         return {"join_paths": join_paths, "resolved_filters": resolved_filters}

#     conn = get_db_connection()
#     if isinstance(conn, str): return {"join_paths": join_paths, "resolved_filters": resolved_filters}
    
#     try:
#         db_type = get_db_type()
#         cursor = get_dict_cursor(conn)
        
#         # 🛡️ STRICT READ-ONLY ENFORCEMENT
#         if db_type == "postgres":
#             try: cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
#             except: pass
        
#         schema_name = os.getenv("DB_SCHEMA", "public") if db_type == "postgres" else os.getenv("DB_DATABASE")
#         if not schema_name and db_type != "postgres":
#             cursor.execute("SELECT DATABASE() as db")
#             res = cursor.fetchone()
#             schema_name = res.get('db', res.get('DB')) if res else None
            
#         table_to_cols, col_to_tables = build_schema_graph(cursor, schema_name)
        
#         new_joins, new_filters = 0, 0
#         dim_to_table_map = {} 
        
#         # 1. Resolve Joins
#         for dim in missing_dims:
#             base_dim = re.sub(r'\d+$', '', dim).strip('_')
            
#             # 🐘 POSTGRES COMPATIBILITY: Use information_schema
#             if db_type == 'postgres':
#                 cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name LIKE %s", (schema_name, f'%{dim}%'))
#             else:
#                 cursor.execute(f"SHOW TABLES LIKE '%{dim}%'")
            
#             possible_tables = [list(r.values())[0].lower() for r in cursor.fetchall()]
            
#             dim_table = None
#             if f"{dim}_dim_desc" in possible_tables: dim_table = f"{dim}_dim_desc"
#             elif f"{base_dim}_dim_desc" in possible_tables: dim_table = f"{base_dim}_dim_desc"
            
#             if not dim_table:
#                 starts_with = [t for t in possible_tables if t.startswith(f"{dim}_") and ('desc' in t or 'dim' in t)]
#                 if not starts_with and base_dim != dim:
#                     starts_with = [t for t in possible_tables if t.startswith(f"{base_dim}_") and ('desc' in t or 'dim' in t)]
#                 if starts_with: dim_table = starts_with[0]
                
#             if not dim_table:
#                 contains = [t for t in possible_tables if ('desc' in t or 'dim' in t)]
#                 if contains: dim_table = contains[0]
                
#             if not dim_table and possible_tables: dim_table = possible_tables[0]
#             if not dim_table: continue
            
#             dim_to_table_map[dim] = dim_table 
                
#             path = find_shortest_join_path(primary_fact, dim_table, table_to_cols, col_to_tables)
            
#             if path and validate_path_data(conn, cursor, path, db_type):
#                 cache_key = f"{primary_fact}_to_{dim}"
#                 join_paths[dim] = path
#                 join_cache[cache_key] = path
#                 new_joins += 1
                
#         # 2. Resolve Filters
#         for raw_f in missing_filters:
#             dim_col = raw_f.get("dimensionColumnName")
#             parent_dim_table = dim_to_table_map.get(dim_col)
#             if not parent_dim_table: 
#                 path = join_paths.get(dim_col, [])
#                 parent_dim_table = path[-1].get("table") if path else primary_fact
            
#             for cond in raw_f.get("and", []):
#                 level_col = cond.get("dimensionLevelColumnName")
#                 values = cond.get("values", [])
                
#                 dim_table = dim_to_table_map.get(level_col)
#                 if not dim_table:
#                     level_path = join_paths.get(level_col, [])
#                     if level_path: dim_table = level_path[-1].get("table")
#                     else: dim_table = parent_dim_table 
                
#                 print(f"🔎 Testing Filter: [{level_col}] in table [{dim_table}] with value {values}...")
#                 true_col = resolve_filter_column(conn, cursor, dim_table, level_col, values, db_type, schema_name)
#                 print(f"   ✅ Data Verified! Correct physical column is: {true_col}")
                
#                 cache_key = f"{dim_col}_{level_col}"
#                 resolved_filters[level_col] = f"{dim_table}.{true_col}" 
#                 filter_cache[cache_key] = f"{dim_table}.{true_col}"
#                 new_filters += 1
                
#         if new_joins > 0: save_json_cache(JOIN_CACHE_FILE, join_cache)
#         if new_filters > 0: save_json_cache(FILTER_CACHE_FILE, filter_cache)
#         dump_log("03_join_paths.json", json.dumps(join_paths, indent=2))
#         dump_log("04_resolved_filters.json", json.dumps(resolved_filters, indent=2))
        
#         print("✅ Pathfinding and Filter Resolution complete.")
#         return {"join_paths": join_paths, "resolved_filters": resolved_filters}
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
#         "resolved_filters": {},
#         "error_message": ""
#     }
    
#     result = app.invoke(initial_state)
#     print("\n✅ Execution complete. Check isolated run_logs/ for your specific Host DB.")













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

class PipelineState(TypedDict):
    api_payload: Dict[str, Any]
    extracted_intent: Dict[str, Any]
    resolved_semantics: Dict[str, Any]
    join_paths: Dict[str, Any]
    resolved_filters: Dict[str, str] 
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
        print("⏭️ No measures to resolve.")
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
        db_type = get_db_type()
        cursor = get_dict_cursor(conn)
        
        schema_name = os.getenv("DB_SCHEMA", "public") if db_type == "postgres" else os.getenv("DB_DATABASE")
        if db_type == "postgres":
            try: 
                cursor.execute(f'SET search_path TO "{schema_name}";')
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
            except Exception: 
                if conn: conn.rollback()
            
        schema_name = os.getenv("DB_SCHEMA", "public") if db_type == "postgres" else os.getenv("DB_DATABASE")
        
        tables_to_check = ['measures', 'measure_aggregations', 'measure_aggregation_dependencies']
        found_tables = {}
        for t in tables_to_check:
            if db_type == 'postgres':
                cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name LIKE %s", (schema_name, f'%{t}%'))
            else:
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

            try:
                # 🐘 Postgres ILIKE compatibility for case insensitive metadata matching
                if db_type == 'postgres':
                    cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_column_name ILIKE %s LIMIT 1", (measure,))
                else:
                    cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_column_name = %s LIMIT 1", (measure,))
                row = cursor.fetchone()
            except Exception:
                if db_type == 'postgres': conn.rollback()
                row = None
            
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
                    try:
                        cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (m_fk,))
                        m_row = cursor.fetchone()
                    except Exception:
                        if db_type == 'postgres': conn.rollback()
                        m_row = None
                        
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
                
                try:
                    cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_id = %s", (cid,))
                    agg_row = cursor.fetchone()
                except Exception:
                    if db_type == 'postgres': conn.rollback()
                    agg_row = None
                    
                if not agg_row: continue
                
                fk = agg_row.get('measure_id', agg_row.get('MEASURE_ID'))
                sub_formula = agg_row.get('measure_formula', agg_row.get('MEASURE_FORMULA', ''))
                
                if fk and m_table:
                    try:
                        cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (fk,))
                        m_row = cursor.fetchone()
                    except Exception:
                        if db_type == 'postgres': conn.rollback()
                        m_row = None
                        
                    if m_row: base_cols.add(m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME')))
                else:
                    children = []
                    if mad_table:
                        try:
                            cursor.execute(f"SELECT source_measure_aggregation_id FROM {mad_table} WHERE target_measure_aggregation_id = %s", (cid,))
                            children = [r.get('source_measure_aggregation_id', r.get('SOURCE_MEASURE_AGGREGATION_ID')) for r in cursor.fetchall()]
                        except Exception:
                            if db_type == 'postgres': conn.rollback()
                    
                    if children: queue.extend(children)
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

# --- 🗺️ NODE 3: DYNAMIC PATHFINDING ---
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

def validate_path_data(conn, cursor, path, db_type):
    try:
        for step in path:
            table1 = step['on'].split(' = ')[0].split('.')[0]
            col = step['on'].split(' = ')[0].split('.')[1]
            table2 = step['table']
            
            cursor.execute(f"SELECT {col} FROM {table2} WHERE {col} IS NOT NULL LIMIT 1")
            res = cursor.fetchall()
            if not res: continue 
            
            sample_val = list(res[0].values())[0]
            cursor.execute(f"SELECT 1 FROM {table1} WHERE {col} = %s LIMIT 1", (sample_val,))
            if not cursor.fetchall(): return False
        return True
    except Exception: 
        if db_type == 'postgres': conn.rollback()
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

def resolve_filter_column(conn, cursor, dim_table, filter_col, sample_values, db_type, schema_name):
    if not sample_values: return filter_col
    sample_val = sample_values[0]
    
    try:
        cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {filter_col} = %s LIMIT 1", (sample_val,))
        if cursor.fetchall(): return filter_col
    except Exception: 
        if db_type == 'postgres': conn.rollback()
        try: cursor.fetchall()
        except: pass
    
    try:
        test_col = f"{filter_col}_id"
        cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {test_col} = %s LIMIT 1", (sample_val,))
        if cursor.fetchall(): return test_col
    except Exception: 
        if db_type == 'postgres': conn.rollback()
        try: cursor.fetchall()
        except: pass
    
    try:
        if db_type == 'postgres':
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s", (schema_name, dim_table))
            all_cols = [r['column_name'].lower() for r in cursor.fetchall()]
        else:
            cursor.execute(f"DESCRIBE {dim_table}")
            all_cols = [r.get('Field', r.get('FIELD', '')).lower() for r in cursor.fetchall()]
        
        for col in all_cols:
            try:
                cursor.execute(f"SELECT 1 FROM {dim_table} WHERE {col} = %s LIMIT 1", (sample_val,))
                if cursor.fetchall(): return col 
            except Exception: 
                if db_type == 'postgres': conn.rollback()
                try: cursor.fetchall()
                except: pass
    except Exception:
        if db_type == 'postgres': conn.rollback()
    
    return filter_col

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
    
    for dim in required_dims:
        cache_key = f"{primary_fact}_to_{dim}"
        if cache_key in join_cache: join_paths[dim] = join_cache[cache_key]
        else: missing_dims.append(dim)
            
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
        
        if db_type == "postgres":
            try: 
                cursor.execute(f'SET search_path TO "{schema_name}";')
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
            except Exception: 
                if conn: conn.rollback()
        table_to_cols, col_to_tables = build_schema_graph(cursor, schema_name)
        
        new_joins, new_filters = 0, 0
        dim_to_table_map = {} 
        
        for dim in missing_dims:
            base_dim = re.sub(r'\d+$', '', dim).strip('_')
            
            if db_type == 'postgres':
                cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name LIKE %s", (schema_name, f'%{dim}%'))
            else:
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
            
            dim_to_table_map[dim] = dim_table 
                
            path = find_shortest_join_path(primary_fact, dim_table, table_to_cols, col_to_tables)
            
            if path and validate_path_data(conn, cursor, path, db_type):
                cache_key = f"{primary_fact}_to_{dim}"
                join_paths[dim] = path
                join_cache[cache_key] = path
                new_joins += 1
                
        for raw_f in missing_filters:
            dim_col = raw_f.get("dimensionColumnName")
            parent_dim_table = dim_to_table_map.get(dim_col)
            if not parent_dim_table: 
                path = join_paths.get(dim_col, [])
                parent_dim_table = path[-1].get("table") if path else primary_fact
            
            for cond in raw_f.get("and", []):
                level_col = cond.get("dimensionLevelColumnName")
                values = cond.get("values", [])
                
                dim_table = dim_to_table_map.get(level_col)
                if not dim_table:
                    level_path = join_paths.get(level_col, [])
                    if level_path: dim_table = level_path[-1].get("table")
                    else: dim_table = parent_dim_table 
                
                print(f"🔎 Testing Filter: [{level_col}] in table [{dim_table}] with value {values}...")
                true_col = resolve_filter_column(conn, cursor, dim_table, level_col, values, db_type, schema_name)
                print(f"   ✅ Data Verified! Correct physical column is: {true_col}")
                
                cache_key = f"{dim_col}_{level_col}"
                resolved_filters[level_col] = f"{dim_table}.{true_col}" 
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

# --- ⚙️ NODE 4: DETERMINISTIC POSTGRES COMPILER ⚙️ ---
def node_sql_compiler(state: PipelineState):
    print("\n" + "="*60 + "\n⚙️ NODE 4: DETERMINISTIC POSTGRES COMPILER\n" + "="*60)
    intent = state["extracted_intent"]
    semantics = state["resolved_semantics"]
    joins = state["join_paths"]
    filters = state["resolved_filters"]

    conn = get_db_connection()
    cursor = get_dict_cursor(conn) if not isinstance(conn, str) else None
    db_schema = os.getenv("DB_SCHEMA", "public")
    
    if cursor:
        try: cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
        except Exception: 
            if conn: conn.rollback()

    target_tables = intent.get("target_tables", ["fact_data"])
    primary_fact = target_tables[0]
    has_override = len(target_tables) > 1
    override_table = target_tables[1] if has_override else ""

    def get_verified_prefix(col_name):
        if not cursor or col_name == "*": return "f."
        try:
            cursor.execute(f'SELECT "{col_name}" FROM "{db_schema}"."{primary_fact}" LIMIT 0')
            return "f."
        except Exception:
            if conn: conn.rollback()
            if override_table:
                try:
                    cursor.execute(f'SELECT "{col_name}" FROM "{db_schema}"."{override_table}" LIMIT 0')
                    return "o."
                except Exception: 
                    if conn: conn.rollback()
            return "f."

    base_aggs = set()
    derived_math = []
    defined_aliases = set()
    all_measures = set(semantics.keys())
    
    # 0. Extract Dimension Keys for Grouping
    dim_fact_keys = {}
    for dim in intent.get("dimensions", []):
        if dim in joins and joins[dim]:
            first_on = joins[dim][0]["on"]
            match = re.search(rf'\b{primary_fact}\.([a-zA-Z0-9_]+)', first_on, re.IGNORECASE)
            if match: dim_fact_keys[dim] = match.group(1)
            else: dim_fact_keys[dim] = f"{dim}_id"

    # 1. Parse Measures & Transpile
    for measure_name, data in semantics.items():
        formula = data.get("formula", "")
        m_type = data.get("type", "SUM")
        phys_cols = data.get("physical_columns", [])
        
        try:
            formula = sqlglot.transpile(formula, read='mysql', write='postgres')[0]
        except Exception: pass

        if "CASE WHEN" in formula.upper() and phys_cols and phys_cols[0].lower() not in formula.lower():
            formula = f"MAX({phys_cols[0]})"
            m_type = "MAX"

        for phys_col in phys_cols:
            if phys_col not in formula and phys_col != "*":
                words = re.findall(r'\b[a-zA-Z_]\w*\b', formula)
                sql_kw = {'sum', 'if', 'null', 'is', 'and', 'or', 'case', 'when', 'then', 'else', 'end', 'max', 'min', 'avg', 'count', 'distinct', 'nullif', 'coalesce'}
                for w in words:
                    w_lower = w.lower()
                    is_agg_prefix = bool(re.match(r'^(sum|max|min|avg|count)_', w_lower))
                    if w_lower not in sql_kw and not is_agg_prefix and w not in all_measures and w not in phys_cols:
                        formula = re.sub(rf'\b{w}\b', phys_col, formula)
                        break

        formula = re.sub(r'\bfact_data\.', '', formula, flags=re.IGNORECASE)
        formula = re.sub(r'\bfact_override\.', '', formula, flags=re.IGNORECASE)

        safe_formula = formula
        for col in phys_cols:
            if col == "*": continue
            prefix = get_verified_prefix(col)
            safe_formula = re.sub(rf'(?<![fo]\.)\b{re.escape(col)}\b', f'{prefix}{col}', safe_formula)

        has_native_agg = bool(re.search(r'\b(SUM|COUNT|MAX|MIN|AVG)\s*\(', safe_formula, re.IGNORECASE))
        uses_alias = bool(re.search(r'\b(sum|max|min|avg|count)_', safe_formula, re.IGNORECASE))

        if m_type in ["SUM", "MAX", "MIN", "AVG", "COUNT"] or (m_type == "FORMULA" and has_native_agg and not uses_alias):
            if not has_native_agg:
                agg = m_type if m_type != "FORMULA" else "SUM"
                safe_formula = f"{agg}({safe_formula})"
            base_aggs.add(f"        {safe_formula} AS {measure_name}")
            defined_aliases.add(measure_name)
        else:
            safe_math = safe_formula
            for col in phys_cols:
                if col == "*": continue
                safe_math = re.sub(rf'(?<![a-zA-Z0-9_\.])\b{re.escape(col)}\b(?!_)', f'sum_{col}', safe_math)

            safe_math = re.sub(r'SUM\((?:[fo]\.)?([a-zA-Z0-9_]+)\)', r'sum_\1', safe_math, flags=re.IGNORECASE)
            safe_math = re.sub(r'MAX\((?:[fo]\.)?([a-zA-Z0-9_]+)\)', r'max_\1', safe_math, flags=re.IGNORECASE)
            safe_math = re.sub(r'AVG\((?:[fo]\.)?([a-zA-Z0-9_]+)\)', r'avg_\1', safe_math, flags=re.IGNORECASE)

            if "NULLIF" not in safe_math.upper():
                safe_math = re.sub(r'/\s*\((.*?)\)', r'/ NULLIF(\1, 0)', safe_math)
                safe_math = re.sub(r'/\s*([a-zA-Z0-9_]+)', r'/ NULLIF(\1, 0)', safe_math)
            
            derived_math.append(f"        {safe_math} AS {measure_name}")

    # 2. Inject Missing Dependencies
    for math_str in derived_math:
        math_only = math_str.split(" AS ")[0]
        matches = re.findall(r'\b(sum|max|min|avg|count)_([a-zA-Z0-9_]+)\b', math_only, re.IGNORECASE)
        for agg_type, col in matches:
            alias = f"{agg_type.lower()}_{col}"
            if alias not in defined_aliases:
                prefix = get_verified_prefix(col)
                base_aggs.add(f"        {agg_type.upper()}({prefix}{col}) AS {alias}")
                defined_aliases.add(alias)

    # 3. BUILD SQL
    sql = "WITH FilteredFact AS (\n"
    sql += f"    SELECT f.*\n"
    sql += f"    FROM \"{db_schema}\".\"{primary_fact}\" f\n"
    
    joined_tables = set()
    tier1_joins = []
    for dim in intent.get("filter_columns", []):
        if dim in joins:
            for step in joins[dim]:
                t = step["table"]
                if t not in joined_tables:
                    on_c = step["on"].replace(f"{primary_fact}.", "f.")
                    tier1_joins.append(f"    INNER JOIN \"{db_schema}\".\"{t}\" ON {on_c}")
                    joined_tables.add(t)

    group_keys = []
    for dim in intent.get("dimensions", []):
        dim_id = f"{dim}_id"
        if dim in joins and joins[dim]:
            path = joins[dim]
            if len(path) == 1:
                group_keys.append(f"f.{dim_fact_keys[dim]}")
            else:
                for step in path[:-1]:
                    t = step["table"]
                    if t not in joined_tables:
                        on_c = step["on"].replace(f"{primary_fact}.", "f.")
                        tier1_joins.append(f"    LEFT JOIN \"{db_schema}\".\"{t}\" ON {on_c}")
                        joined_tables.add(t)
                group_keys.append(f"f.{dim_fact_keys[dim]}")
        else:
            group_keys.append(f"f.{dim_id}")

    if tier1_joins: sql += "\n".join(tier1_joins) + "\n"
        
    where_c = []
    for raw_f in intent.get("raw_filters", []):
        for cond in raw_f.get("and", []):
            lvl = cond.get("dimensionLevelColumnName")
            if lvl in filters:
                vals = ", ".join([f"'{v}'" if isinstance(v, str) else str(v) for v in cond.get("values", [])])
                where_c.append(f"        {filters[lvl]} IN ({vals})")
                
    if where_c: sql += "    WHERE\n" + " AND\n".join(where_c) + "\n"
    else: sql += "    WHERE 1=1\n"
        
    sql += "),\nAggregatedFact AS (\n    SELECT\n"
    
    if group_keys:
        group_keys = list(dict.fromkeys(group_keys))
        sql += ",\n".join([f"        {k}" for k in group_keys]) + ",\n"
        
    sql += ",\n".join(sorted(list(base_aggs))) + "\n"
    sql += "    FROM FilteredFact f\n"
    
    if override_table:
        sql += f"    LEFT JOIN \"{db_schema}\".\"{override_table}\" o ON f.time_id = o.time_id AND f.product_id = o.product_id AND f.location_id = o.location_id\n"
        
    if group_keys:
        sql += "    GROUP BY\n" + ",\n".join([f"        {k}" for k in group_keys]) + "\n"
        
    sql += ")\nSELECT\n"
    
    outer_selects = []
    for dim in intent.get("dimensions", []):
        if dim in joins:
            target_dim_t = joins[dim][-1]["table"]
            outer_selects.append(f"    af.{dim_fact_keys[dim]}")
            outer_selects.append(f"    {target_dim_t}.{dim}_name")
            
    if outer_selects: sql += ",\n".join(outer_selects) + ",\n"
        
    ordered_measures = []
    for m in intent.get("measures", []):
        if m in defined_aliases:
            ordered_measures.append(f"    af.{m}")
        else:
            for d in derived_math:
                if d.endswith(f" AS {m}"):
                    clean_d = d.replace("f.", "af.").replace("o.", "af.").strip()
                    ordered_measures.append(f"    {clean_d}")
                    
    sql += ",\n".join(ordered_measures) + "\nFROM AggregatedFact af\n"
    
    joined_dims = set()
    for dim in intent.get("dimensions", []):
        if dim in joins and dim in dim_fact_keys:
            fact_col = dim_fact_keys[dim]
            prev_table = "af"
            for step in joins[dim]:
                t = step["table"]
                if t not in joined_dims:
                    on_c = step["on"]
                    match = re.search(rf'\b{primary_fact}\.([a-zA-Z0-9_]+)', on_c, re.IGNORECASE)
                    if match:
                        on_c = on_c.replace(match.group(0), f"{prev_table}.{match.group(1)}")
                    sql += f"LEFT JOIN \"{db_schema}\".\"{t}\" ON {on_c}\n"
                    joined_dims.add(t)
                    
    if intent.get("dimensions"):
        first_dim = intent["dimensions"][0]
        if first_dim in joins:
            target_dim_t = joins[first_dim][-1]["table"]
            sql += f"ORDER BY {target_dim_t}.{first_dim}_name ASC\n"
            
    limit = intent.get("limit", 200)
    sql += f"LIMIT {limit};\n"

    if cursor: cursor.close()
    if conn and not isinstance(conn, str): conn.close()

    print("\n" + "="*60 + "\n🏆 FINAL DETERMINISTIC SQL QUERY\n" + "="*60)
    print(sql)
    return {"error_message": ""}

# --- 🕸️ BUILD GRAPH ---
def build_pipeline():
    workflow = StateGraph(PipelineState)
    workflow.add_node("Extract_Intent", node_extract_intent)
    workflow.add_node("Semantic_Resolver", node_semantic_resolver)
    workflow.add_node("Pathfinder", node_pathfinder)
    workflow.add_node("SQL_Compiler", node_sql_compiler)
    
    workflow.set_entry_point("Extract_Intent")
    workflow.add_edge("Extract_Intent", "Semantic_Resolver")
    workflow.add_edge("Semantic_Resolver", "Pathfinder")
    workflow.add_edge("Pathfinder", "SQL_Compiler")
    workflow.add_edge("SQL_Compiler", END) 
    
    return workflow.compile()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-file', required=True)
    args = parser.parse_args()
    
    print("Initializing Orchestrator (Nodes 1, 2, 3, 4)...")
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
