import os
import json
import sqlglot
import sqlglot.expressions as exp
from dotenv import load_dotenv
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

load_dotenv()

CACHE_FILE = os.path.join("run_logs", "semantic_cache.json")

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_cache(cache_data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
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
    Takes a list of UI measures. Returns a dictionary mapping them to exact formulas and physical columns.
    Uses JSON Caching, Graph Traversal, and AST Parsing.
    """
    cache = load_cache()
    resolved = {}
    missing_measures = []

    # 1. Fast Cache Retrieval
    for m in requested_measures:
        if m in cache:
            resolved[m] = cache[m]
        else:
            missing_measures.append(m)

    if not missing_measures:
        return resolved

    print(f"🔍 [Semantic Resolver] Cache Miss for {len(missing_measures)} measures. Traversing BI Metadata Graph...")
    
    conn = get_db_connection()
    if isinstance(conn, str):
        print(f"🔥 Database Connection Error: {conn}")
        return resolved

    db_type = get_db_type()
    
    try:
        cursor = get_dict_cursor(conn)
        
        # 2. 🚀 FIX: Use the proven table discovery loop from the diagnostic script
        tables_to_check = ['measures', 'measure_aggregations', 'measure_aggregation_dependencies']
        found_tables = {}

        for t in tables_to_check:
            cursor.execute(f"SHOW TABLES LIKE '%{t}%'")
            res = cursor.fetchall()
            if res:
                found_tables[t] = list(res[0].values())[0]

        ma_table = found_tables.get('measure_aggregations')
        mad_table = found_tables.get('measure_aggregation_dependencies')
        m_table = found_tables.get('measures')

        if not ma_table:
            print("⚠️ [Semantic Resolver] No metadata tables found. Falling back to direct mappings.")
            for m in missing_measures: resolved[m] = {"formula": m, "physical_columns": [m], "type": "SUM"}
            return resolved

        # 3. Hybrid Resolve Each Missing Measure
        new_discoveries = 0
        for measure in missing_measures:
            
            # Handle UI Artifacts automatically without querying the DB
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
                # If not in the graph, assume it's a physical column (LLM will verify later)
                resolved[measure] = {"formula": measure, "physical_columns": [measure], "type": "SUM"}
                new_discoveries += 1
                continue

            curr_id = row.get('measure_aggregation_id', row.get('MEASURE_AGGREGATION_ID'))
            raw_formula = row.get('measure_formula', row.get('MEASURE_FORMULA', ''))
            m_type = str(row.get('measure_aggregation_type', row.get('MEASURE_AGGREGATION_TYPE', 'SUM'))).upper()

            # If it's just a direct physical mapping
            if m_type != "FORMULA":
                m_fk = row.get('measure_id', row.get('MEASURE_ID'))
                if m_fk and m_table:
                    cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (m_fk,))
                    m_row = cursor.fetchone()
                    if m_row:
                        phys_col = m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME'))
                        resolved[measure] = {"formula": phys_col, "physical_columns": [phys_col], "type": m_type}
                        new_discoveries += 1
                        continue
                
                resolved[measure] = {"formula": measure, "physical_columns": [measure], "type": m_type}
                new_discoveries += 1
                continue

            # If it IS a FORMULA, unpack it (The BFS Traversal)
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
                        # AST Fallback!
                        extracted = extract_columns_from_formula(sub_formula)
                        for c in extracted: base_cols.add(c)

            # Clean columns (strip table prefixes if AST caught them)
            clean_cols = [c.split('.')[-1] for c in base_cols if c]
            
            resolved[measure] = {
                "formula": raw_formula,
                "physical_columns": clean_cols,
                "type": "FORMULA"
            }
            new_discoveries += 1

        # 4. Save to Cache
        if new_discoveries > 0:
            cache.update(resolved)
            save_cache(cache)
            print(f"💾 [Semantic Resolver] Saved {new_discoveries} new definitions to run_logs/semantic_cache.json.")

        return resolved

    except Exception as e:
        print(f"🔥 Semantic Resolver Error: {e}")
        return resolved
    finally:
        if 'cursor' in locals(): cursor.close()
        if hasattr(conn, 'close'): conn.close()

if __name__ == "__main__":
    # Test the Resolver natively
    test_measures = ["st_final_plan", "normal_mape_adjUPLT", "user_baseline_forecast", "row_count"]
    print("\n--- Resolver Output ---")
    print(json.dumps(resolve_measures(test_measures), indent=2))
