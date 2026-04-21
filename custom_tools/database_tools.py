import os
import socket
import time
import mysql.connector
from mysql.connector import Error as MySQLError
import psycopg2
import psycopg2.extras
from psycopg2 import Error as PostgresError
from langchain_core.tools import tool
from dotenv import load_dotenv

load_dotenv()

def get_db_type():
    return os.getenv("DB_TYPE", "mysql").lower()

def get_db_connection(retries=3):
    """Establishes a connection with robust retry logic and enhanced logging."""
    db_type = get_db_type()
    host = os.getenv("DB_HOST")
    port = int(os.getenv("DB_PORT", 5432 if db_type == "postgres" else 3306))
    user = os.getenv("DB_USER")
    database = os.getenv("DB_DATABASE")
    
    print(f"\n🔌 [DB LOG] Attempting to connect to {db_type.upper()} database at {host}:{port}...")
    print(f"🔌 [DB LOG] User: {user} | Target DB: {database}")

    for attempt in range(retries):
        try:
            if db_type == "postgres":
                print(f"🔌 [DB LOG] Resolving IPv4 for Postgres host: {host} (Attempt {attempt+1}/{retries})")
                try:
                    ipv4_address = socket.gethostbyname(host)
                    print(f"🔌 [DB LOG] Resolved to IPv4: {ipv4_address}")
                except Exception as e:
                    print(f"❌ [DB LOG] DNS Resolution Failed: {e}")
                    return f"Database Connection Error (DNS Resolution Failed): {e}"

                sslmode = os.getenv("DB_SSL_MODE", "require")
                print(f"🔌 [DB LOG] Using SSL Mode: {sslmode}")
                
                conn = psycopg2.connect(
                    host=host,
                    hostaddr=ipv4_address, 
                    user=user,
                    password=os.getenv("DB_PASSWORD"),
                    port=port,
                    database=database,
                    sslmode=sslmode,
                    connect_timeout=10
                )
                conn.set_session(readonly=True)
                print("✅ [DB LOG] PostgreSQL Connection Established Successfully!\n")
                return conn
                
            else: # Default to MySQL
                print(f"🔌 [DB LOG] Connecting to MySQL... (Attempt {attempt+1}/{retries})")
                conn = mysql.connector.connect(
                    user=user,
                    password=os.getenv("DB_PASSWORD"),
                    host=host,
                    port=port,
                    database=database,
                    client_flags=[mysql.connector.ClientFlag.FOUND_ROWS] 
                )
                print("✅ [DB LOG] MySQL Connection Established Successfully!\n")
                return conn
                
        except (MySQLError, PostgresError, Exception) as err:
            print(f"⚠️ [DB LOG] Connection attempt {attempt + 1} failed: {err}")
            if attempt < retries - 1:
                print("⏳ [DB LOG] Retrying in 2 seconds...")
                time.sleep(2) 
                continue
            print("❌ [DB LOG] All connection attempts exhausted.\n")
            return f"Database Connection Error: {err}"

def get_dict_cursor(conn):
    if get_db_type() == "postgres":
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor(dictionary=True)

@tool
def get_table_schema(table_name: str) -> str: 
    """Returns columns for a table"""
    return ""

@tool
def find_column_location(column_name: str) -> str: 
    """Finds which table a column is in"""
    return ""

@tool
def get_measure_definition(measure_names: str) -> str: 
    """Returns formulas for measures"""
    return ""
