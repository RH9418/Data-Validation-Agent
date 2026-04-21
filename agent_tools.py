import os
from typing import List, Dict, Any
from langchain_core.tools import tool
from custom_tools.database_tools import get_db_connection, get_dict_cursor, get_db_type

# --- HELPER FUNCTION ---
import re

def execute_read_query(query: str, params: tuple = None) -> List[Dict[str, Any]]:
    """Safely executes a STRICTLY READ-ONLY query and returns a list of dictionaries."""
    
    # 🛡️ APPLICATION LEVEL ENFORCEMENT: Regex Sanitizer
    # Block any query that contains DML or DDL commands outside of a strict SELECT context.
    # Note: We use word boundaries \b to avoid blocking column names like 'update_date'
    forbidden_keywords = re.compile(
        r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|MERGE|REPLACE)\b', 
        re.IGNORECASE
    )
    
    if forbidden_keywords.search(query):
        print(f"🚨 SECURITY BLOCK: Attempted mutative query blocked -> {query}")
        return [{"error": "SECURITY VIOLATION: Query contains forbidden mutative keywords. Only SELECT is allowed."}]

    conn = get_db_connection()
    if isinstance(conn, str):
        return [{"error": f"Connection failed: {conn}"}]
    
    try:
        cursor = get_dict_cursor(conn)
        
        # 🛡️ SESSION LEVEL ENFORCEMENT
        # (Syntax varies slightly by DB, this is standard Postgres)
        cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
        
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
