import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

def find_missing_measure():
    print("🔌 Connecting to PostgreSQL...")
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_DATABASE", "postgres")
    )
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    db_schema = os.getenv("DB_SCHEMA", "stage_da2_dataset1")
    
    print(f"\n--- Searching for 'actual_sales_and_roy_fcst_dollars' in {db_schema}.measure_aggregations ---")
    
    try:
        cursor.execute(f"""
            SELECT measure_aggregation_id, measure_aggregation_column_name, measure_formula, measure_aggregation_type
            FROM "{db_schema}"."measure_aggregations"
            WHERE measure_aggregation_column_name ILIKE '%actual_sales_and_roy_fcst_dollars%';
        """)
        
        results = cursor.fetchall()
        if results:
            print("✅ FOUND IT! Here is the true metadata:")
            for r in results:
                print(f"  Name: {r['measure_aggregation_column_name']}")
                print(f"  Formula: {r['measure_formula']}")
                print(f"  Type: {r['measure_aggregation_type']}")
        else:
            print("❌ NOT FOUND. The BI tool is asking for a measure that does not exist in the database.")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    find_missing_measure()
