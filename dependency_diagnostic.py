import os
import json
import sqlglot
import sqlglot.expressions as exp
from dotenv import load_dotenv
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

load_dotenv()

def extract_columns_from_formula(formula_string):
    """Uses an AST parser to extract raw column names from a SQL formula."""
    if not formula_string: return []
    try:
        tree = sqlglot.parse_one(formula_string)
        columns = [node.name for node in tree.find_all(exp.Column)]
        return list(set(columns))
    except Exception as e:
        print(f"      [AST Parser Error on '{formula_string}']: {e}")
        return []

def run_diagnostics():
    print("🕵️‍♂️ Initiating Hybrid BI Metadata + AST Diagnostic...\n" + "="*60)
    conn = get_db_connection()
    if isinstance(conn, str): 
        print(f"🔥 DB Connection Failed: {conn}")
        return

    db_type = get_db_type()
    schema_name = os.getenv("DB_SCHEMA", "public") if db_type == "postgres" else os.getenv("DB_DATABASE")

    try:
        cursor = get_dict_cursor(conn)
        if not schema_name and db_type != "postgres":
            cursor.execute("SELECT DATABASE() as db")
            res = cursor.fetchone()
            schema_name = res.get('db', res.get('DB')) if res else None

        print(f"🔌 [LOG] Active Schema: {schema_name}")

        tables_to_check = ['measures', 'measure_aggregations', 'measure_aggregation_dependencies']
        found_tables = {}

        for t in tables_to_check:
            cursor.execute(f"SHOW TABLES LIKE '%{t}%'")
            res = cursor.fetchall()
            if res: 
                found_tables[t] = list(res[0].values())[0]
                print(f"✅ [LOG] Found table: {found_tables[t]}")
            else:
                print(f"❌ [LOG] Table missing: {t}")

        ma_table = found_tables.get('measure_aggregations')
        mad_table = found_tables.get('measure_aggregation_dependencies')
        m_table = found_tables.get('measures')

        if not ma_table: 
            print("❌ [LOG] Aborting: 'measure_aggregations' table not found.")
            return

        print("\n🔍 [LOG] Searching for 'normal_mape_adjUPLT' in measure_aggregations...")
        
        cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_column_name = 'normal_mape_adjUPLT' LIMIT 1")
        formula_row = cursor.fetchone()

        if not formula_row: 
            print(f"❌ [LOG] Aborting: 'normal_mape_adjUPLT' does not exist in {ma_table} on this database!")
            
            # Let's print what DOES exist just to be helpful
            cursor.execute(f"SELECT measure_aggregation_column_name FROM {ma_table} WHERE measure_aggregation_type = 'FORMULA' LIMIT 5")
            print("💡 [LOG] Here are some formulas that DO exist in this DB:")
            for r in cursor.fetchall():
                col = r.get('measure_aggregation_column_name', r.get('MEASURE_AGGREGATION_COLUMN_NAME'))
                print(f"   - {col}")
            return
            
        target_id = formula_row.get('measure_aggregation_id', formula_row.get('MEASURE_AGGREGATION_ID'))
        col_name = formula_row.get('measure_aggregation_column_name', formula_row.get('MEASURE_AGGREGATION_COLUMN_NAME'))
        print(f"\n🎯 [LOG] Target Formula: '{col_name}' (Agg ID: {target_id})")
        
        print("\n🧪 [LOG] Initiating Hybrid Recursive Traversal + AST Parsing...")
        
        queue = [target_id]
        visited = set()
        base_physical_columns = set()

        while queue:
            curr_id = queue.pop(0)
            if curr_id in visited: continue
            visited.add(curr_id)

            cursor.execute(f"SELECT * FROM {ma_table} WHERE measure_aggregation_id = %s", (curr_id,))
            agg_row = cursor.fetchone()
            if not agg_row: continue

            agg_name = agg_row.get('measure_aggregation_column_name', agg_row.get('MEASURE_AGGREGATION_COLUMN_NAME'))
            m_fk = agg_row.get('measure_id', agg_row.get('MEASURE_ID'))
            raw_formula = agg_row.get('measure_formula', agg_row.get('MEASURE_FORMULA', ''))

            if m_fk:
                cursor.execute(f"SELECT measure_column_name FROM {m_table} WHERE measure_id = %s", (m_fk,))
                m_row = cursor.fetchone()
                if m_row:
                    phys_col = m_row.get('measure_column_name', m_row.get('MEASURE_COLUMN_NAME'))
                    print(f"   🌿 LEAF REACHED: '{agg_name}' maps perfectly to Physical Column: [{phys_col}]")
                    base_physical_columns.add(phys_col)
            else:
                print(f"   🔄 HOP: '{agg_name}' (ID: {curr_id}) is a nested formula.")
                
                if mad_table:
                    cursor.execute(f"SELECT source_measure_aggregation_id FROM {mad_table} WHERE target_measure_aggregation_id = %s", (curr_id,))
                    children = [r.get('source_measure_aggregation_id', r.get('SOURCE_MEASURE_AGGREGATION_ID')) for r in cursor.fetchall()]
                else:
                    children = []
                
                if children:
                    print(f"      -> Found children in graph: {children}")
                    queue.extend(children)
                else:
                    print(f"      -> ⚠️ Graph Dead End! Missing metadata for ID {curr_id}.")
                    print(f"      -> 🛠️ Activating AST Parser on formula string: {raw_formula}")
                    extracted_cols = extract_columns_from_formula(raw_formula)
                    
                    if extracted_cols:
                        print(f"      -> 🎯 AST Parser extracted base columns: {extracted_cols}")
                        for c in extracted_cols:
                            base_physical_columns.add(c)
                    else:
                        print(f"      -> ❌ AST Parser failed to extract columns.")

        print("\n🏁 [LOG] Final Unpacked Base Physical Columns Required for SQL Synthesis:")
        for col in base_physical_columns:
            print(f"   ✅ {col}")

        print("\n============================================================")

    except Exception as e:
        print(f"🔥 Fatal Error during diagnostics: {e}")
    finally:
        if 'cursor' in locals(): cursor.close()
        if hasattr(conn, 'close'): conn.close()

if __name__ == "__main__":
    run_diagnostics()
