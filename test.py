import os
import re
import time
from typing import List, Dict, Any
from dotenv import load_dotenv

# Import your existing connection tools
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

load_dotenv()

# --- STANDALONE SAFE QUERY EXECUTOR ---
def safe_execute_query(query: str, timeout_ms: int = 10000) -> List[Dict[str, Any]]:
    """Executes a query with a strict DB-level timeout."""
    
    conn = get_db_connection()
    if isinstance(conn, str):
        return [{"error": f"Connection failed: {conn}"}]
    
    db_type = (get_db_type() or "postgres").lower()
    
    try:
        cursor = get_dict_cursor(conn)
        
        # 🛡️ INJECT TIMEOUTS
        try:
            if db_type == 'postgres':
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
                cursor.execute(f"SET statement_timeout = {timeout_ms};") 
            elif db_type == 'mysql':
                cursor.execute("SET SESSION TRANSACTION READ ONLY;")
                cursor.execute(f"SET max_execution_time = {timeout_ms};") 
        except Exception as e:
            print(f"Warning: Could not set timeout variables: {e}")
        
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

# --- THE TESTS ---
def run_safety_tests():
    db_schema = os.getenv("DB_SCHEMA", "public")
    q = '"' if (get_db_type() or "postgres").lower() == "postgres" else '`'
    
    print("🛡️ STARTING DATABASE SAFETY TESTS...\n")

    # ---------------------------------------------------------
    # TEST 1: Normal Execution
    # ---------------------------------------------------------
    print("="*60)
    print("▶️ TEST 1: Normal Fast Query")
    query_fast = f"SELECT * FROM {q}{db_schema}{q}.{q}fact_data{q} LIMIT 5;"
    
    start_time = time.time()
    res1 = safe_execute_query(query_fast)
    elapsed = time.time() - start_time
    
    if res1 and "error" not in res1[0]:
        print(f"✅ SUCCESS: Query returned {len(res1)} rows in {elapsed:.2f} seconds.")
    else:
        print(f"❌ FAILED: {res1[0]['error']}")

    # ---------------------------------------------------------
    # TEST 2: The "Zero-Resource" EXPLAIN approach
    # ---------------------------------------------------------
    print("\n" + "="*60)
    print("▶️ TEST 2: EXPLAIN (Syntax Check without Execution)")
    # We prepend EXPLAIN to the query
    query_explain = f"EXPLAIN SELECT * FROM {q}{db_schema}{q}.{q}fact_data{q} WHERE {q}actual_sales_dollars{q} > 0 LIMIT 5;"
    
    start_time = time.time()
    res2 = safe_execute_query(query_explain)
    elapsed = time.time() - start_time
    
    if res2 and "error" not in res2[0]:
        print(f"✅ SUCCESS: EXPLAIN generated the query plan in {elapsed:.2f} seconds without touching data!")
        # Print a snippet of the query plan
        plan_str = str(res2[0])[:100] + "..."
        print(f"   Plan Snippet: {plan_str}")
    else:
        print(f"❌ FAILED: {res2[0]['error']}")

    # ---------------------------------------------------------
    # TEST 3: The Cartesian Product Nightmare (Timeout Test)
    # ---------------------------------------------------------
    print("\n" + "="*60)
    print("▶️ TEST 3: The 10-Second Timeout (Simulating a bad AI Cross Join)")
    print("⏳ Waiting for database to forcefully kill the rogue query...")
    
    # Intentional nightmare query: cross-joining three massive tables without an ON clause.
    query_nightmare = f"""
        SELECT a.* 
        FROM {q}{db_schema}{q}.{q}fact_data{q} a
        CROSS JOIN {q}{db_schema}{q}.{q}fact_data{q} b
        CROSS JOIN {q}{db_schema}{q}.{q}fact_override{q} c
    """
    
    start_time = time.time()
    res3 = safe_execute_query(query_nightmare, timeout_ms=10000) # 10 seconds
    elapsed = time.time() - start_time
    
    if res3 and "error" in res3[0]:
        error_msg = res3[0]['error'].lower()
        if "timeout" in error_msg or "canceling statement due to" in error_msg or "time exceeded" in error_msg:
            print(f"🛡️ ✅ SUCCESS! Database successfully KILLED the rogue query after {elapsed:.2f} seconds!")
            print(f"   Captured Error: {res3[0]['error']}")
        else:
            print(f"⚠️ Query failed, but for a different reason: {res3[0]['error']}")
    else:
        print(f"❌ TERRIBLE FAILURE: The query actually finished in {elapsed:.2f}s without timing out. (Your tables might be empty!)")

if __name__ == "__main__":
    run_safety_tests()
