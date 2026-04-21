import os
import json
import sqlglot
import sqlglot.expressions as exp
from custom_tools.database_tools import get_db_connection, get_dict_cursor

CACHE_FILE = os.path.join("run_logs", "semantic_cache.json")

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(cache_data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=4)

def extract_columns_from_formula(formula_string):
    if not formula_string: return []
    try:
        tree = sqlglot.parse_one(formula_string)
        return list(set([node.name for node in tree.find_all(exp.Column)]))
    except Exception:
        return []

def resolve_measures(requested_measures):
    """
    Takes a list of measures from the API. Returns a dictionary mapping each measure
    to its exact SQL formula and the physical columns required to calculate it.
    """
    cache = load_cache()
    resolved = {}
    missing_measures = []

    # 1. Check Cache First (Zero Cost, Zero Latency)
    for m in requested_measures:
        if m in cache:
            resolved[m] = cache[m]
        else:
            missing_measures.append(m)

    if not missing_measures:
        return resolved

    print(f"🔍 [Semantic Resolver] Cache Miss for {len(missing_measures)} measures. Introspecting database...")
    
    conn = get_db_connection()
    if isinstance(conn, str):
        print(f"🔥 Database Connection Error: {conn}")
        return resolved

    try:
        cursor = get_dict_cursor(conn)
        
        # 2. Identify the dynamic names of the BI Metadata tables
        cursor.execute("SHOW TABLES LIKE '%measure_aggregations%'")
        res1 = cursor.fetchall()
        ma_table = list(res1[0].values())[0] if res1 else None

        cursor.execute("SHOW TABLES LIKE '%measure_aggregation_dependencies%'")
        res2 = cursor.fetchall()
        mad_table = list(res2[0].values())[0] if res2 else None

        cursor.execute("SHOW TABLES LIKE '%measures%'")
        res3 = cursor.fetchall()
        m_table = list(res3[0].values())[0] if res3 else None

        if not ma_table:
            print("⚠️ No measure_aggregations table found. Returning empty resolution.")
            return resolved

        # 3. Hybrid Resolve each missing measure
        for measure in missing_measures:
            # Check UI artifacts first
            if measure in ['row_count']:
                resolved[measure] = {"formula": "COUNT(*)", "physical_columns": ["*"], "type": "COUNT"}
                continue
            if measure in ['is_editable', 'pt_editable', 'imputed_flag', 'planner_tag']:
                resolved[measure] = {"formula": "NULL", "physical_columns": [], "type": "FORMULA"}
                continue

            cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_column_name = %s LIMIT 1", (measure,))
            row = cursor.fetchone()
            
            if not row:
                # If it's not in the aggregations table, we assume it's a direct physical column
                # The Orchestrator LLM will verify this later.
                resolved[measure] = {"formula": measure, "physical_columns": [measure], "type": "SUM"}
                continue

            curr_id = row.get('measure_aggregation_id')
            raw_formula = row.get('measure_formula', '')
            m_type = str(row.get('measure_aggregation_type', 'SUM')).upper()

            # If it's a direct mapping (not a formula)
            if m_type != "FORMULA":
                m_fk = row.get('measure_id')
                if m_fk and m_table:
                    cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (m_fk,))
                    m_row = cursor.fetchone()
                    if m_row:
                        phys_col = m_row['measure_column_name']
                        resolved[measure] = {"formula": phys_col, "physical_columns": [phys_col], "type": m_type}
                        continue
                
                # Fallback if no FK
                resolved[measure] = {"formula": measure, "physical_columns": [measure], "type": m_type}
                continue

            # If it IS a FORMULA, we must unpack it using our hybrid logic
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

                fk = agg_row.get('measure_id')
                sub_formula = agg_row.get('measure_formula', '')
                
                if fk and m_table:
                    cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (fk,))
                    m_row = cursor.fetchone()
                    if m_row: base_cols.add(m_row['measure_column_name'])
                else:
                    children = []
                    if mad_table:
                        cursor.execute(f"SELECT source_measure_aggregation_id FROM {mad_table} WHERE target_measure_aggregation_id = %s", (cid,))
                        children = [r['source_measure_aggregation_id'] for r in cursor.fetchall()]
                    
                    if children:
                        queue.extend(children)
                    else:
                        # AST Fallback
                        extracted = extract_columns_from_formula(sub_formula)
                        for c in extracted: base_cols.add(c)

            # Clean up the AST columns (e.g., stripping table prefixes if they exist)
            clean_cols = [c.split('.')[-1] for c in base_cols if c]
            
            # Save the resolved formula structure
            resolved[measure] = {
                "formula": raw_formula,
                "physical_columns": clean_cols,
                "type": "FORMULA"
            }

        # 4. Save to Cache
        cache.update(resolved)
        save_cache(cache)
        print(f"✅ [Semantic Resolver] Saved {len(missing_measures)} new definitions to cache.")
        return resolved

    except Exception as e:
        print(f"🔥 Semantic Resolver Error: {e}")
        return resolved
    finally:
        if 'cursor' in locals(): cursor.close()
        if hasattr(conn, 'close'): conn.close()

if __name__ == "__main__":
    # Test it!
    test_measures = ["st_final_plan", "user_baseline_forecast", "sum_st_base_fcst", "is_editable"]
    print(json.dumps(resolve_measures(test_measures), indent=2))
