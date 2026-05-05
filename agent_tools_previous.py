import os
from typing import List, Dict, Any
from langchain_core.tools import tool
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type
import difflib

# --- HELPER FUNCTION ---
import re

import re

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
        except Exception as e:
            # Some cloud DBs/poolers reject transaction changes. We rely on the Regex sanitizer as backup.
            pass 
        
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



# --- AGENT TOOLS ---

@tool
def search_tables_by_keyword(keywords: List[str]) -> List[Dict[str, Any]]:
    """
    Searches the database for tables or views that match specific keywords.
    Use this when you need to find tables related to a specific topic (e.g., ['sales', 'forecast', 'map']).
    """
    db_schema = os.getenv("DB_SCHEMA", "public")
    
    # Build dynamic ILIKE clauses for each keyword
    conditions = " OR ".join([f"table_name ILIKE %s" for _ in keywords])
    params = tuple(f"%{kw}%" for kw in keywords)
    
    query = f"""
        SELECT table_name, table_type 
        FROM information_schema.tables 
        WHERE table_schema = '{db_schema}' 
        AND ({conditions})
        LIMIT 50;
    """
    return execute_read_query(query, params)

@tool
def get_table_schema(table_name: str) -> List[Dict[str, Any]]:
    """
    Retrieves the exact column names and data types for a specific table or view.
    Use this to inspect a table's structure before attempting to query it.
    """
    db_schema = os.getenv("DB_SCHEMA", "public")
    query = """
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position;
    """
    return execute_read_query(query, (db_schema, table_name))

@tool
def search_columns_by_keyword(keywords: List[str]) -> List[Dict[str, Any]]:
    """
    Searches the entire database schema for column names matching specific keywords.
    Crucial for finding where aliased metrics (e.g., 'fcst_dollars') physically live.
    """
    db_schema = os.getenv("DB_SCHEMA", "public")
    conditions = " OR ".join([f"column_name ILIKE %s" for _ in keywords])
    params = tuple(f"%{kw}%" for kw in keywords)
    
    query = f"""
        SELECT table_name, column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = '{db_schema}' 
        AND ({conditions})
        LIMIT 100;
    """
    return execute_read_query(query, params)

@tool
def sample_column_data(table_name: str, column_name: str) -> List[Dict[str, Any]]:
    """
    Retrieves up to 10 distinct, non-null values from a specific column.
    Use this to understand the actual shape of the data (e.g., does 'status' contain 'Active', 'A', or '1'?).
    """
    db_schema = os.getenv("DB_SCHEMA", "public")
    # Note: table and column names are injected directly. In a production environment, 
    # ensure these variables are strictly validated to prevent SQL injection, 
    # though the read-only transaction mitigates risk.
    query = f"""
        SELECT DISTINCT "{column_name}" 
        FROM "{db_schema}"."{table_name}" 
        WHERE "{column_name}" IS NOT NULL 
        LIMIT 10;
    """
    return execute_read_query(query)
@tool
def search_table_for_value(table_name: str, search_keyword: str) -> List[Dict[str, Any]]:
    """
    Searches ALL text columns in a specific table for a specific keyword.
    Use this ONLY on mapping tables, data dictionaries, or metadata tables 
    (e.g., 'measure_aggregations', 'override_type_measure_mapping') 
    to find the definition of a derived UI metric.
    """
    db_schema = os.getenv("DB_SCHEMA", "public")
    
    # First, dynamically get all text-based columns from the table
    schema_query = """
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = %s AND table_name = %s 
        AND data_type IN ('text', 'character varying', 'varchar')
    """
    columns_result = execute_read_query(schema_query, (db_schema, table_name))
    
    if not columns_result or "error" in columns_result[0]:
        return columns_result # Return the error if table doesn't exist
        
    text_columns = [row['column_name'] for row in columns_result]
    
    if not text_columns:
        return [{"error": f"No text columns found in {table_name} to search."}]

    # Build a dynamic OR clause to search every text column in that table
    conditions = " OR ".join([f'"{col}" ILIKE %s' for col in text_columns])
    params = tuple(f"%{search_keyword}%" for _ in text_columns)
    
    query = f"""
        SELECT * 
        FROM "{db_schema}"."{table_name}" 
        WHERE {conditions}
        LIMIT 5;
    """
    return execute_read_query(query, params)

import re

@tool
def trace_metric_to_physical(logical_metric: str) -> dict:
    """
    Cross-Dialect tool to trace a logical UI metric to its physical tables/columns.
    Automatically handles aggregation prefixes (sum_, min_, max_, avg_, count_).
    """
    db_schema = os.getenv("DB_SCHEMA", os.getenv("DB_DATABASE", "public"))
    db_type = (get_db_type() or "postgres").lower()
    
    like_op = "ILIKE" if db_type == "postgres" else "LIKE"
    q = '"' if db_type == "postgres" else '`'
    
    print(f"\n🔍 [Tool] Tracing metric: '{logical_metric}' on {db_type.upper()}")
    
    # Helper function to strip aggregation prefixes
    def strip_agg_prefix(col_name: str) -> str:
        prefixes = ('sum_', 'max_', 'min_', 'avg_', 'count_')
        lower_col = col_name.lower()
        for p in prefixes:
            if lower_col.startswith(p):
                return col_name[len(p):] # Returns the string without the prefix
        return col_name

    stripped_metric = strip_agg_prefix(logical_metric)
    
    # Helper to check physical schema
    def check_physical(col_name: str):
        query = f"""
            SELECT table_name, column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = %s AND column_name {like_op} %s
        """
        return execute_read_query(query, (db_schema, col_name))

    # 1. DIRECT PHYSICAL CHECK (Try exact name first)
    phys_results = check_physical(logical_metric)
    if phys_results and "error" not in phys_results[0] and len(phys_results) > 0:
        print(f"✅ [Tool] '{logical_metric}' is a direct physical column.")
        return {"status": "SUCCESS", "type": "PHYSICAL_COLUMN", "metric": logical_metric, "locations": phys_results}
        
    # 2. STRIPPED PHYSICAL CHECK (If it's an aggregation like sum_ly_sales)
    if stripped_metric != logical_metric:
        phys_results_stripped = check_physical(stripped_metric)
        if phys_results_stripped and "error" not in phys_results_stripped[0] and len(phys_results_stripped) > 0:
            print(f"✅ [Tool] '{logical_metric}' is an aggregation. Base column is '{stripped_metric}'.")
            return {
                "status": "SUCCESS", 
                "type": "AGGREGATED_PHYSICAL", 
                "metric": logical_metric, 
                "base_column": stripped_metric,
                "locations": phys_results_stripped
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
    
    if not form_results or "error" in form_results[0] or len(form_results) == 0:
        print(f"❌ [Tool] Failed to find mapping row for '{logical_metric}'. Attempting Advanced Fuzzy Search...")
        
        # --- ROBUST PYTHON FUZZY RANKING ---
        import difflib
        target_tokens = set(logical_metric.lower().split('_'))
        scored_columns = []
        
        # 1. Grab ALL physical columns
        all_cols_query = f"SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s"
        all_cols = execute_read_query(all_cols_query, (db_schema,))
        if not all_cols or "error" in all_cols[0]: all_cols = []
            
        # 2. Grab ALL derived metrics from the semantic layer
        derived_query = f"SELECT 'measure_aggregations' as table_name, measure_aggregation_column_name as column_name FROM {table_path}"
        derived_cols = execute_read_query(derived_query)
        if not derived_cols or "error" in derived_cols[0]: derived_cols = []
            
        # Combine them
        all_candidates = all_cols + derived_cols
        
        for row in all_candidates:
            col_name = str(row['column_name']).lower()
            col_tokens = set(col_name.split('_'))
            
            # A: Token Overlap
            overlap = len(target_tokens.intersection(col_tokens))
            # B: Character Similarity
            similarity = difflib.SequenceMatcher(None, logical_metric.lower(), col_name).ratio()
            
            if overlap >= 2 or similarity > 0.6:
                scored_columns.append({
                    "table_name": row['table_name'],
                    "column_name": row['column_name'],
                    "overlap_score": overlap,
                    "similarity": similarity
                })
                
        # Sort by overlap, then similarity
        scored_columns.sort(key=lambda x: (x['overlap_score'], x['similarity']), reverse=True)
        
        # 3. Deduplicate by column name so we get 5 UNIQUE suggestions
        seen_cols = set()
        unique_suggestions = []
        for s in scored_columns:
            if s['column_name'] not in seen_cols:
                seen_cols.add(s['column_name'])
                unique_suggestions.append(s)
                if len(unique_suggestions) == 5: # Limit to Top 5 unique
                    break
                    
        suggestions_for_llm = [
            {"table_name": s["table_name"], "column_name": s["column_name"]}
            for s in unique_suggestions
        ]
        
        if suggestions_for_llm:
            suggested_names = [s['column_name'] for s in suggestions_for_llm]
            print(f"⚠️ [Tool] Top UNIQUE fuzzy suggestions for '{logical_metric}': {suggested_names}")
            return {
                "status": "FAILED_BUT_SUGGESTS", 
                "error": f"Metric '{logical_metric}' does not exist.",
                "suggestions": suggestions_for_llm
            }
                
        return {"status": "FAILED", "error": f"Could not find exact or fuzzy match for '{logical_metric}'"}
    formula = form_results[0].get("measure_formula", form_results[0].get("MEASURE_FORMULA"))
    agg_type = form_results[0].get("measure_aggregation_type", form_results[0].get("MEASURE_AGGREGATION_TYPE"))
    
    if not formula or str(formula).strip() == "":
        return {"status": "FAILED", "error": f"Formula is empty. Aggregation type was: {agg_type}"}
        
    print(f"✅ [Tool] Found formula: {formula}")
    
    # 4. RECURSIVE DEPENDENCY EXTRACTION
    words = re.findall(r'\b[a-zA-Z_]\w*\b', formula)
    sql_kw = {'sum', 'if', 'null', 'is', 'and', 'or', 'case', 'when', 'then', 'else', 'end', 'max', 'min', 'avg', 'count', 'distinct', 'nullif', 'coalesce', 'true', 'false'}
    potential_columns = list(set([w.lower() for w in words if w.lower() not in sql_kw]))
    
    # 5. FIND LOCATIONS OF DEPENDENCIES (Checking exact AND stripped)
    dependencies = []
    
    # Helper to validate if a table actually has data for a column
    def table_has_data(table, col):
        q2 = '"' if db_type == "postgres" else '`'
        check_q = f"SELECT {q2}{col}{q2} FROM {q2}{db_schema}{q2}.{q2}{table}{q2} WHERE {q2}{col}{q2} IS NOT NULL AND {q2}{col}{q2} != 0 LIMIT 1"
        res = execute_read_query(check_q)
        return res and "error" not in res[0] and len(res) > 0

    for col in potential_columns:
        col_res = check_physical(col)
        valid_locations = []
        
        if col_res and "error" not in col_res[0]:
            for r in col_res:
                # ONLY add the table if it actually has data!
                if table_has_data(r['table_name'], r['column_name']):
                    valid_locations.append(r)
                    
        if not valid_locations:
            s_col = strip_agg_prefix(col)
            if s_col != col:
                s_col_res = check_physical(s_col)
                if s_col_res and "error" not in s_col_res[0]:
                    for r in s_col_res:
                        if table_has_data(r['table_name'], r['column_name']):
                            r["original_formula_term"] = col 
                            valid_locations.append(r)
                            
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
    It automatically discovers the primary key and looks for any Cross-Reference (_xref) 
    bridge tables required to join it to the fact tables.
    """
    db_schema = os.getenv("DB_SCHEMA", os.getenv("DB_DATABASE", "public"))
    db_type = (get_db_type() or "postgres").lower()
    like_op = "ILIKE" if db_type == "postgres" else "LIKE"
    
    print(f"\n🔍 [Tool] Tracing dimension hierarchy for: '{logical_dimension}'")
    
    # 1. Find the Dimension Description Table (e.g., product3 -> product3_dim_desc)
    dim_table_query = f"""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = %s 
        AND table_name {like_op} %s
    """
    # Look for exact match, or standard _dim_desc suffixes
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
    
    # 2. Find the Primary Key of the Dimension Table
    pk_query = f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = %s AND table_name = %s AND column_name {like_op} %s
    """
    pk_res = execute_read_query(pk_query, (db_schema, dim_table, f"%id%"))
    
    # Heuristic: the PK usually starts with the dimension name (e.g., product3_id)
    dim_pk = None
    if pk_res and "error" not in pk_res[0]:
        for row in pk_res:
            if row['column_name'].startswith(logical_dimension):
                dim_pk = row['column_name']
                break
        if not dim_pk and len(pk_res) > 0:
            dim_pk = pk_res[0]['column_name'] # Fallback to first ID column
            
    if not dim_pk:
         return {"status": "FAILED", "error": f"Could not identify Primary Key for '{dim_table}'"}
         
    print(f"✅ [Tool] Found Dimension PK: {dim_pk}")
    
    # 3. Check for XREF Bridge Tables
    # E.g., if dimension is product3, see if product_dim_xref exists
    base_entity = logical_dimension.rstrip('0123456789') # turns 'product3' into 'product'
    xref_table_name = f"{base_entity}_dim_xref"
    
    xref_query = f"""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = %s AND table_name = %s
    """
    xref_res = execute_read_query(xref_query, (db_schema, xref_table_name))
    
    bridge_required = False
    bridge_details = {}
    
    if xref_res and "error" not in xref_res[0] and len(xref_res) > 0:
        print(f"🌉 [Tool] Found Cross-Reference Bridge Table: {xref_table_name}")
        bridge_required = True
        
        # Assume the base entity ID connects to the fact table (e.g., product_id)
        fact_join_key = f"{base_entity}_id" 
        bridge_details = {
            "xref_table": xref_table_name,
            "join_to_fact_on": fact_join_key,
            "join_to_dim_on": dim_pk
        }
        
    return {
        "status": "SUCCESS",
        "logical_dimension": logical_dimension,
        "physical_table": dim_table,
        "primary_key": dim_pk,
        "requires_bridge": bridge_required,
        "bridge_details": bridge_details
    }



# --- MANUAL TESTING BLOCK ---
if __name__ == "__main__":
    print("🧪 Testing Agent Tools...")
    
    print("\n1. Searching for tables containing 'fact' or 'map':")
    print(search_tables_by_keyword.invoke({"keywords": ["fact", "map"]}))
    
    # Replace 'fact_data' with a table you know exists on the host you are testing
    test_table = "fact_data" 
    print(f"\n2. Getting schema for '{test_table}':")
    print(get_table_schema.invoke({"table_name": test_table}))
    
    print("\n3. Searching for columns containing 'fcst' or 'forecast':")
    print(search_columns_by_keyword.invoke({"keywords": ["fcst", "forecast"]}))
    
    # Replace with a valid column from your test table
    test_column = "time_id" 
    print(f"\n4. Sampling data from {test_table}.{test_column}:")
    print(sample_column_data.invoke({"table_name": test_table, "column_name": test_column}))
