import os
import json
import re
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from custom_tools.database_tools import get_db_connection, get_dict_cursor

load_dotenv()

# --- SETUP: Point this to your specific host log directory ---
def get_host_specific_log_dir():
    host = os.getenv("DB_HOST", "unknown_host").replace(".", "_")
    db = os.getenv("DB_DATABASE")
    if not db: db = os.getenv("DB_SCHEMA", "unknown_db")
    return os.path.join("run_logs", f"{host}_{db}")

LOG_DIR = get_host_specific_log_dir()

def load_json(filename):
    filepath = os.path.join(LOG_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# 🚀 THE PYTHON DATA-CLEANER 🚀
def clean_metadata_for_llm(semantics):
    """Sanitizes dirty BI formulas before the LLM sees them."""
    cleaned_semantics = {}
    for m, data in semantics.items():
        formula = data.get("formula", "")
        phys_cols = data.get("physical_columns", [])
        
        # Strip dirty CASE WHEN statements that hide the physical column
        if "CASE WHEN" in formula.upper() and phys_cols and phys_cols[0].lower() not in formula.lower():
            cleaned_semantics[m] = {
                "formula": f"MAX({phys_cols[0]})",
                "physical_columns": phys_cols,
                "type": "MAX"
            }
        else:
            cleaned_semantics[m] = data
            
    return cleaned_semantics

# --- THE AGENTIC COMPILER ---
def synthesize_sql_with_auto_healing(intent, semantics, joins, filters, max_retries=3):
    print("\n" + "="*60 + "\n🧠 NODE 4: AGENTIC SQL SYNTHESIZER\n" + "="*60)
    
    llm = AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_ENDPOINT"),
        openai_api_key=os.getenv("AZURE_API_KEY"),
        openai_api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
        azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
        temperature=0
    )

    system_prompt = f"""You are an elite Data Engineer writing MySQL queries for SingleStore.
You must construct a query using a STRICT Three-Tier CTE architecture to prevent Distributed Join Explosions.

ARCHITECTURE:
Tier 1: FilteredFact
- Use the Primary Fact Table in the FROM clause (alias as 'f').
- INNER JOIN ONLY the dimension/xref tables required for the FILTERS (Do not join everything).
- Apply the FILTERS here (WHERE clause).
- CRITICAL: Use `SELECT f.*` to safely pass all fact columns to the next tier without typing them manually. DO NOT use a naked `SELECT *`.
- If grouping by a parent dimension (e.g. opstudy), you MUST join the xref table here and include `xref_table.parent_id` in your SELECT clause so Tier 2 can group by it!

Tier 2: AggregatedFact
- FROM FilteredFact (alias as 'f').
- If there are secondary fact tables (like fact_override), LEFT JOIN them here using shared granular keys (e.g. ON f.time_id = o.time_id AND f.product_id = o.product_id).
- GROUP BY the requested dimension ID columns.
- Evaluate formulas containing native aggregations here (SUM, MAX, COUNT). Use the 'f.' prefix for standard columns and 'o.' for override columns.
- If a Measure formula requires un-aggregated math (e.g., `A / B`), you MUST wrap the variables in `SUM(A)` and `SUM(B)` here in Tier 2!

Tier 3: Final Output
- FROM AggregatedFact
- LEFT JOIN the remaining dimension tables to get the descriptive name columns (e.g., `_name`).
- Calculate Derived Formulas here using the aggregated outputs from Tier 2.
- ALWAYS wrap the denominator of division operations in NULLIF(..., 0).

DO NOT hallucinate. Respond ONLY with the raw SQL query enclosed in ```sql ... ``` block."""

    cleaned_semantics = clean_metadata_for_llm(semantics)

    human_prompt = f"""Here are the empirically validated database mappings:

EXTRACTED INTENT (Targets, Dimensions, Limit, Sort):
{json.dumps(intent, indent=2)}

RESOLVED SEMANTICS (Measures & Math Formulas):
{json.dumps(cleaned_semantics, indent=2)}

JOIN PATHS (Proven Relational Connections):
{json.dumps(joins, indent=2)}

RESOLVED FILTERS (Verified Physical Columns for WHERE clause):
{json.dumps(filters, indent=2)}

Write the SQL query."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt)
    ]
    
    conn = get_db_connection()
    cursor = get_dict_cursor(conn) if not isinstance(conn, str) else None

    # The Validation Execution Loop
    for attempt in range(max_retries + 1):
        print(f"🔄 Generation Attempt {attempt + 1}...")
        response = llm.invoke(messages)
        
        sql_match = re.search(r"```sql\n(.*?)\n```", response.content, re.DOTALL | re.IGNORECASE)
        if not sql_match:
            sql_query = response.content.replace("```sql", "").replace("```", "").strip()
        else:
            sql_query = sql_match.group(1).strip()
            
        print(f"\n📝 Draft Query generated:\n{sql_query}\n")
        
        if not cursor:
            print("⚠️ Database connection failed. Cannot run validation loop.")
            return sql_query

        try:
            print("🔬 Running LIMIT 0 Semantic Validation on database...")
            
            clean_query = sql_query.strip().rstrip(";")
            clean_query = re.sub(r'\s+LIMIT\s+\d+$', '', clean_query, flags=re.IGNORECASE)
            validation_wrapper = f"{clean_query}\nLIMIT 0;"
            
            cursor.execute(validation_wrapper)
            cursor.fetchall() 
            print("✅ Semantic Validation Succeeded! The query is mathematically and structurally flawless.")
            return sql_query
            
        except Exception as e:
            error_msg = str(e)
            print(f"❌ Database Error Caught: {error_msg}")
            try: cursor.fetchall() 
            except: pass
            
            if attempt == max_retries:
                print("🛑 Max retries reached. Returning last failed query.")
                return sql_query
                
            print("🛠️ Passing error back to LLM for Auto-Healing...")
            messages.append(AIMessage(content=f"```sql\n{sql_query}\n```"))
            
            healing_prompt = f"The database threw this error:\n{error_msg}\n\nReview the Architecture rules. "
            if "Unknown column" in error_msg:
                healing_prompt += "If the column is a metric, check your SELECT f.* list in Tier 1. If it's a dimension name, use '_name', not '_desc'. Ensure you prefixed columns correctly."
            elif "aggregate function" in error_msg or "GROUP BY" in error_msg:
                healing_prompt += "You likely put a SUM() or COUNT() in the outer query instead of Tier 2, or you forgot to add a dimension ID to the GROUP BY."
            elif "Duplicate column" in error_msg:
                healing_prompt += "You used a naked `SELECT *` which caused overlapping columns. Use `SELECT f.*`."
                
            healing_prompt += " CRITICAL: NEVER delete a requested dimension or measure to 'fix' an error. Fix the alias or logic instead. Return ONLY the corrected SQL block."
            messages.append(HumanMessage(content=healing_prompt))

    if cursor: cursor.close()
    if conn and not isinstance(conn, str): conn.close()

if __name__ == "__main__":
    print(f"Loading empirical metadata from {LOG_DIR}...")
    
    intent = load_json("01_extracted_intent.json")
    semantics = load_json("02_resolved_semantics.json")
    joins = load_json("03_join_paths.json")
    filters = load_json("04_resolved_filters.json")
    
    if not intent:
        print("❌ Error: Could not find JSON files. Ensure Nodes 1-3 have run first.")
        exit(1)
        
    final_query = synthesize_sql_with_auto_healing(intent, semantics, joins, filters)
    
    print("\n" + "="*60 + "\n🏆 FINAL AUTO-HEALED SQL QUERY\n" + "="*60)
    print(final_query)
