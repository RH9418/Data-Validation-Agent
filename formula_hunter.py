import os
from dotenv import load_dotenv
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

load_dotenv()

def looks_like_sql_formula(text):
    """Heuristic to check if a string contains math or SQL functions."""
    if not text or not isinstance(text, str): return False
    text = text.upper()
    sql_keywords = ['SUM(', 'MAX(', 'MIN(', 'COUNT(', 'COALESCE(', 'CASE WHEN', 'NULLIF(']
    math_ops = [' + ', ' - ', ' * ', ' / ']
    
    has_keyword = any(kw in text for kw in sql_keywords)
    has_math = any(op in text for op in math_ops)
    return has_keyword or has_math

def hunt_for_formulas():
    print("🕵️‍♂️ Initiating Automated Formula Hunter...\n" + "="*60)
    conn = get_db_connection()
    if isinstance(conn, str):
        return # The DB logger already printed the error

    db_type = get_db_type()
    schema_name = os.getenv("DB_SCHEMA", "public") if db_type == "postgres" else os.getenv("DB_DATABASE")
    like_operator = "ILIKE" if db_type == "postgres" else "LIKE"
    
    try:
        cursor = get_dict_cursor(conn)
        
        # Step 1: Broad Metadata Scan for suspicious column names
        print(f"🔍 Scanning INFORMATION_SCHEMA in '{schema_name}' for formula-like columns...")
        query = f"""
            SELECT TABLE_NAME, COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s 
            AND (
                COLUMN_NAME {like_operator} '%formula%' OR 
                COLUMN_NAME {like_operator} '%calc%' OR 
                COLUMN_NAME {like_operator} '%expr%' OR
                TABLE_NAME {like_operator} '%measure%agg%'
            )
        """
        cursor.execute(query, (schema_name,))
        candidates = cursor.fetchall()
        
        if not candidates:
            print("❌ No obvious formula columns found in metadata.")
            return
            
        print(f"✅ Found {len(candidates)} candidate columns. Verifying content...\n")
        
        # Step 2: Content Verification
        valid_formula_tables = {}
        
        for cand in candidates:
            t_name = cand.get('TABLE_NAME', cand.get('table_name'))
            c_name = cand.get('COLUMN_NAME', cand.get('column_name'))
            
            try:
                # Sample the data to see if it contains actual SQL math
                cursor.execute(f"SELECT {c_name} FROM {t_name} WHERE {c_name} IS NOT NULL LIMIT 10")
                rows = cursor.fetchall()
                
                valid_formulas = [row[c_name] for row in rows if looks_like_sql_formula(row[c_name])]
                
                if valid_formulas:
                    if t_name not in valid_formula_tables:
                        valid_formula_tables[t_name] = []
                    valid_formula_tables[t_name].append(c_name)
                    print(f"🎯 BINGO! Table '{t_name}' contains valid SQL formulas in column '{c_name}'.")
                    print(f"   Example: {valid_formulas[0][:80]}...\n")
            except Exception as e:
                # Ignore tables we can't query (e.g. permissions)
                continue
                
        if valid_formula_tables:
            print("============================================================")
            print("🚀 FORMULA DISCOVERY COMPLETE")
            print("The system will dynamically use these tables to resolve virtual measures!")
            print(valid_formula_tables)
        else:
            print("❌ Columns found, but row data did not look like mathematical formulas.")

    except Exception as e:
        print(f"🔥 Query Error: {e}")
    finally:
        if 'cursor' in locals(): cursor.close()
        if hasattr(conn, 'close'): conn.close()

if __name__ == "__main__":
    hunt_for_formulas()
