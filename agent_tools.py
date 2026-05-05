import os
import re
import difflib
from typing import List, Dict, Any
from langchain_core.tools import tool
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

# --- HELPER FUNCTION ---
def execute_read_query(query: str, params: tuple = None) -> List[Dict[str, Any]]:
    """Safely executes a STRICTLY READ-ONLY query across multiple DB dialects."""
    
    # 🛡️ APPLICATION LEVEL ENFORCEMENT: Regex Sanitizer
    forbidden_keywords = re.compile(
        r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|MERGE|REPLACE)\b', 
        re.IGNORECASE
    )
    
    if forbidden_keywords.search(query):
        print(f"🚨 SECURITY BLOCK: Attempted mutative query blocked -> {query}")
        return [{"error": "SECURITY VIOLATION: Query contains forbidden mutative keywords."}]

    conn = get_db_connection()
    if isinstance(conn, str):
        return [{"error": f"Connection failed: {conn}"}]
    
    db_type = (get_db_type() or "postgres").lower()
    
    try:
        cursor = get_dict_cursor(conn)
        
        # 🛡️ SESSION LEVEL ENFORCEMENT (Dialect Aware)
        try:
            if db_type == 'postgres':
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
            elif db_type == 'mysql':
                cursor.execute("SET SESSION TRANSACTION READ ONLY;")
        except Exception:
            pass # Rely on Regex sanitizer as backup for poolers that reject session vars
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
            
        results = cursor.fetchall()
        return results
        
    except Exception as e:
        if conn:
            conn.rollback()
        return [{"error": str(e)}]
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if conn and hasattr(conn, 'close'):
            conn.close()

# --- SMART AGENT TOOLS ---

@tool
def trace_metric_to_physical(logical_metric: str) -> dict:
    """
    Cross-Dialect tool to trace a logical UI metric to its physical tables/columns.
    Automatically handles aggregation prefixes, formulas, missing columns, and hierarchy dependencies.
    Prioritizes 'fact_data' and 'fact_override'.
    """
    db_schema = os.getenv("DB_SCHEMA", os.getenv("DB_DATABASE", "public"))
    db_type = (get_db_type() or "postgres").lower()
    
    like_op = "ILIKE" if db_type == "postgres" else "LIKE"
    q = '"' if db_type == "postgres" else '`'
    
    print(f"\n🔍 [Tool] Tracing metric: '{logical_metric}' on {db_type.upper()}")
    
    def strip_agg_prefix(col_name: str) -> str:
        prefixes = ('sum_', 'max_', 'min_', 'avg_', 'count_')
        lower_col = col_name.lower()
        for p in prefixes:
            if lower_col.startswith(p):
                return col_name[len(p):]
        return col_name

    stripped_metric = strip_agg_prefix(logical_metric)
    
    # ---------------------------------------------------------
    # IMPROVEMENT 1: The Priority Sorter
    # ---------------------------------------------------------
    def check_physical(col_name: str):
        query = f"""
            SELECT table_name, column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = %s AND column_name {like_op} %s
        """
        res = execute_read_query(query, (db_schema, col_name))
        
        if res and "error" not in res[0]:
            def get_priority(table_name):
                t = table_name.lower()
                # Gold Standard Tables
                if t == 'fact_data': return 1
                if t == 'fact_override': return 2
                # Penalize noisy/temp/audit tables heavily
                if any(x in t for x in ['tejas', 'audit', 'calculation', 'bckp', 'stg_']): return 99
                # Standard fallback for tables like stage_game_fact
                return 10
                
            res.sort(key=lambda x: get_priority(x['table_name']))
            
        return res

    # 1. DIRECT PHYSICAL CHECK
    phys_results = check_physical(logical_metric)
    if phys_results and "error" not in phys_results[0] and len(phys_results) > 0:
        print(f"✅ [Tool] '{logical_metric}' is a direct physical column (Top pick: {phys_results[0]['table_name']}).")
        # Return ONLY the top prioritized table
        return {"status": "SUCCESS", "type": "PHYSICAL_COLUMN", "metric": logical_metric, "locations": [phys_results[0]]}
        
    # 2. STRIPPED PHYSICAL CHECK
    if stripped_metric != logical_metric:
        phys_results_stripped = check_physical(stripped_metric)
        if phys_results_stripped and "error" not in phys_results_stripped[0] and len(phys_results_stripped) > 0:
            print(f"✅ [Tool] '{logical_metric}' is an aggregation. Base column is '{stripped_metric}' (Top pick: {phys_results_stripped[0]['table_name']}).")
            return {
                "status": "SUCCESS", 
                "type": "AGGREGATED_PHYSICAL", 
                "metric": logical_metric, 
                "base_column": stripped_metric,
                "locations": [phys_results_stripped[0]] # Return ONLY the top prioritized table
            }

    # 3. DERIVED METRIC CHECK
    print(f"🔍 [Tool] '{logical_metric}' is not a physical column. Hunting for formula...")
    table_path = f"{q}{db_schema}{q}.{q}measure_aggregations{q}"
    
    query_formula = f"""
        SELECT measure_formula, measure_aggregation_type 
        FROM {table_path} 
        WHERE measure_aggregation_column_name {like_op} %s
    """
    form_results = execute_read_query(query_formula, (logical_metric,))
    
    # --- FUZZY FALLBACK LOGIC ---
    if not form_results or "error" in form_results[0] or len(form_results) == 0:
        print(f"❌ [Tool] Failed to find mapping row for '{logical_metric}'. Attempting Advanced Fuzzy Search...")
        
        import difflib
        target_tokens = set(logical_metric.lower().split('_'))
        scored_columns = []
        
        all_cols_query = f"SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s"
        all_cols = execute_read_query(all_cols_query, (db_schema,))
        if not all_cols or "error" in all_cols[0]: all_cols = []
            
        derived_query = f"SELECT 'measure_aggregations' as table_name, measure_aggregation_column_name as column_name FROM {table_path}"
        derived_cols = execute_read_query(derived_query)
        if not derived_cols or "error" in derived_cols[0]: derived_cols = []
            
        all_candidates = all_cols + derived_cols
        
        for row in all_candidates:
            col_name = str(row['column_name']).lower()
            col_tokens = set(col_name.split('_'))
            
            overlap = len(target_tokens.intersection(col_tokens))
            similarity = difflib.SequenceMatcher(None, logical_metric.lower(), col_name).ratio()
            
            if overlap >= 2 or similarity > 0.6:
                scored_columns.append({
                    "table_name": row['table_name'],
                    "column_name": row['column_name'],
                    "overlap_score": overlap,
                    "similarity": similarity
                })
                
        scored_columns.sort(key=lambda x: (x['overlap_score'], x['similarity']), reverse=True)
        
        seen_cols = set()
        unique_suggestions = []
        for s in scored_columns:
            if s['column_name'] not in seen_cols:
                seen_cols.add(s['column_name'])
                unique_suggestions.append(s)
                if len(unique_suggestions) == 5: 
                    break
                    
        suggestions_for_llm = [{"table_name": s["table_name"], "column_name": s["column_name"]} for s in unique_suggestions]
        
        if suggestions_for_llm:
            return {"status": "FAILED_BUT_SUGGESTS", "error": f"Metric '{logical_metric}' does not exist.", "suggestions": suggestions_for_llm}
                
        return {"status": "FAILED", "error": f"Could not find exact or fuzzy match for '{logical_metric}'"}

    formula = form_results[0].get("measure_formula", form_results[0].get("MEASURE_FORMULA"))
    
    if not formula or str(formula).strip() == "":
        return {"status": "FAILED", "error": "Formula is empty."}
        
    print(f"✅ [Tool] Found formula: {formula}")
    
    # 4. FORMULA PARSING
        # 4. FORMULA PARSING
    words = re.findall(r'\b[a-zA-Z_]\w*\b', formula)
    
    # Expanded SQL Blacklist to prevent function/type names from being queried as columns
    sql_kw = {
        # Standard Logical / Aggregations (Existing)
        'sum', 'if', 'null', 'is', 'and', 'or', 'case', 'when', 'then', 'else', 'end', 
        'max', 'min', 'avg', 'count', 'distinct', 'nullif', 'coalesce', 'true', 'false',
        
        # Functions found in your Schema Map
        'to_char', 'cast', 'extract', 
        
        # Casting Types and Syntax found in your Schema Map
        'as', 'date', 'decimal', 'int',
        
        # Recommended future-proofing for Data Warehouses
        'not', 'in', 'numeric', 'varchar', 'char', 'float', 'double', 'round', 'trunc'
    }
    
    potential_columns = list(set([w.lower() for w in words if w.lower() not in sql_kw]))

    
    # 5. ROBUST DEPENDENCY RESOLVER
    dependencies = []
    
    def table_has_data(table, col):
        check_q = f"SELECT {q}{col}{q} FROM {q}{db_schema}{q}.{q}{table}{q} LIMIT 1"
        res = execute_read_query(check_q)
        return res and "error" not in res[0] and len(res) > 0

    for col in potential_columns:
        valid_locations = []
        
        candidates_to_try = [col]
        stripped = strip_agg_prefix(col)
        if stripped != col:
            candidates_to_try.append(stripped)
            
        if col.endswith('s'):
            candidates_to_try.append(col[:-1])
            if stripped != col and stripped.endswith('s'):
                candidates_to_try.append(stripped[:-1])
                
        for candidate in candidates_to_try:
            col_res = check_physical(candidate)
            if col_res and "error" not in col_res[0]:
                for r in col_res:
                    if table_has_data(r['table_name'], r['column_name']):
                        r["original_formula_term"] = col
                        r["resolved_term"] = candidate
                        valid_locations.append(r)
                        
                        # ---------------------------------------------------------
                        # IMPROVEMENT 2: The Early Exit
                        # Stop iterating through tables once we find the gold standard
                        # ---------------------------------------------------------
                        break 
                
            if valid_locations:
                break # Stop searching candidates once we found a valid table
                
        for loc in valid_locations:
            resolved_col = loc.get("resolved_term", col)
            match = re.match(r'^([a-zA-Z]+)\d+_id$', resolved_col)
            if match:
                base_entity = match.group(1) 
                xref_table = f"{base_entity}_dim_xref"
                
                xref_check = execute_read_query(f"SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name = %s", (db_schema, xref_table))
                
                if xref_check and "error" not in xref_check[0] and len(xref_check) > 0:
                    loc["requires_bridge"] = True
                    loc["bridge_table"] = xref_table
                    loc["bridge_join_to_fact"] = f"{base_entity}_id"
                    print(f"🌉 [Tool] Auto-discovered bridge for {resolved_col}: {xref_table}")
                    
        dependencies.extend(valid_locations)
        
    return {
        "status": "SUCCESS",
        "type": "DERIVED_FORMULA",
        "metric": logical_metric,
        "formula": formula,
        "physical_dependencies": dependencies
    }

    

@tool
def trace_dimension_hierarchy(logical_dimension: str, target_fact_tables: List[str]) -> dict:
    """
    Maps a logical dimension (e.g., 'product3', 'month') to its physical dimension table.
    Discovers Primary Keys, Descriptive Columns, and Cross-Reference (_xref) bridge tables automatically.
    """
    db_schema = os.getenv("DB_SCHEMA", os.getenv("DB_DATABASE", "public"))
    db_type = (get_db_type() or "postgres").lower()
    like_op = "ILIKE" if db_type == "postgres" else "LIKE"
    
    print(f"\n🔍 [Tool] Tracing dimension hierarchy for: '{logical_dimension}'")
    
    # 1. Find the Dimension Table
    dim_table_query = f"SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name {like_op} %s"
    possible_names = [logical_dimension, f"{logical_dimension}_dim_desc", f"{logical_dimension}_dim"]
    dim_table = None
    
    for name in possible_names:
        res = execute_read_query(dim_table_query, (db_schema, name))
        if res and "error" not in res[0] and len(res) > 0:
            dim_table = res[0]['table_name']
            break
            
    if not dim_table:
        return {"status": "FAILED", "error": f"Could not find a dimension table for '{logical_dimension}'"}
        
    print(f"✅ [Tool] Found Dimension Table: {dim_table}")
    
    # 2. Find the Primary Key
    pk_query = f"SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name {like_op} %s"
    pk_res = execute_read_query(pk_query, (db_schema, dim_table, f"%id%"))
    
    dim_pk = None
    if pk_res and "error" not in pk_res[0]:
        for row in pk_res:
            if row['column_name'].startswith(logical_dimension):
                dim_pk = row['column_name']
                break
        if not dim_pk and len(pk_res) > 0:
            dim_pk = pk_res[0]['column_name'] 
            
    if not dim_pk:
         return {"status": "FAILED", "error": f"Could not identify Primary Key for '{dim_table}'"}
         
    print(f"✅ [Tool] Found Dimension PK: {dim_pk}")
    
    # ---------------------------------------------------------
    # NEW: Find the Display Column (Descriptive Text)
    # ---------------------------------------------------------
    desc_query = f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = %s AND table_name = %s 
        AND column_name != %s 
        AND (column_name {like_op} %s OR column_name {like_op} %s)
        LIMIT 1
    """
    desc_res = execute_read_query(desc_query, (db_schema, dim_table, dim_pk, '%name%', '%desc%'))
    
    display_col = dim_pk # Fallback to ID if no name column exists
    if desc_res and "error" not in desc_res[0] and len(desc_res) > 0:
        display_col = desc_res[0]['column_name']
        
    print(f"✅ [Tool] Found Display Column: {display_col}")

    # 3. Find the Bridge/XREF Table
    base_entity = logical_dimension.rstrip('0123456789') 
    xref_table_name = f"{base_entity}_dim_xref"
    base_key = f"{base_entity}_id"
    
    xref_query = f"SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name = %s"
    xref_res = execute_read_query(xref_query, (db_schema, xref_table_name))
    
    bridge_required = False
    bridge_details = {}
    
    if xref_res and "error" not in xref_res[0] and len(xref_res) > 0:
        print(f"🌉 [Tool] Found Cross-Reference Bridge Table: {xref_table_name}")
        bridge_required = True
        bridge_details = {
            "xref_table": xref_table_name,
            "join_to_fact_on": base_key,
            "join_to_dim_on": dim_pk
        }
        
    return {
        "status": "SUCCESS",
        "logical_dimension": logical_dimension,
        "physical_table": dim_table,
        "primary_key": dim_pk,
        "base_key": base_key,               # Explicitly returned for Agent 1's JOIN condition
        "display_column": display_col,      # Explicitly returned for Agent 1's SELECT statement
        "requires_bridge": bridge_required,
        "bridge_details": bridge_details
    }


# --- EXPLORATION TOOLS (For manual debugging) ---
@tool
def search_tables_by_keyword(keywords: List[str]) -> List[Dict[str, Any]]:
    """Searches for tables matching keywords."""
    db_schema = os.getenv("DB_SCHEMA", "public")
    conditions = " OR ".join([f"table_name ILIKE %s" for _ in keywords])
    params = tuple(f"%{kw}%" for kw in keywords)
    query = f"SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = '{db_schema}' AND ({conditions}) LIMIT 50;"
    return execute_read_query(query, params)

@tool
def get_table_schema(table_name: str) -> List[Dict[str, Any]]:
    """Retrieves column names and data types for a table."""
    db_schema = os.getenv("DB_SCHEMA", "public")
    query = "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position;"
    return execute_read_query(query, (db_schema, table_name))
