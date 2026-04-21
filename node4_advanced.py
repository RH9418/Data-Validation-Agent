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

# 🚀 THE CONDITIONAL BLUEPRINT GENERATOR 🚀
def generate_blueprint(intent, semantics, joins, filters):
    target_tables = intent.get("target_tables", ["fact_data"])
    primary_fact = target_tables[0]
    has_multiple_facts = len(target_tables) > 1
    
    blueprint = "=== SQL CONSTRUCTION BLUEPRINT ===\n\n"
    
    if has_multiple_facts:
        blueprint += "ARCHITECTURE: THREE-TIER CTE (Required to prevent distributed join explosion)\n\n"
        
        blueprint += "1. TIER 1 CTE ('FilteredFact'):\n"
        blueprint += f"- FROM {primary_fact} (alias as 'f')\n"
        blueprint += "- INNER JOIN ONLY the dimension/xref tables required for the FILTERS (see section 4).\n"
        blueprint += "- Apply the FILTERS here (WHERE clause).\n"
        blueprint += f"- CRITICAL: Use `SELECT f.*` to safely pass all fact columns to the next tier without typing them manually. DO NOT use a naked `SELECT *`.\n\n"
        
        blueprint += "2. TIER 2 CTE ('AggregatedFact'):\n"
        blueprint += "- FROM FilteredFact (alias as 'f')\n"
        blueprint += f"- LEFT JOIN {target_tables[1]} (alias as 'o') ON f.time_id = o.time_id AND f.product_id = o.product_id AND f.location_id = o.location_id\n"
        blueprint += "- GROUP BY the ID columns of the requested dimensions.\n"
        blueprint += "- Apply Base Aggregations here (SUM, MAX, COUNT). Use the 'f.' prefix for standard columns and 'o.' for override columns.\n"
        blueprint += "- Evaluate formulas containing native aggregations here.\n\n"
        
        blueprint += "3. TIER 3 (OUTER QUERY):\n"
        blueprint += "- FROM AggregatedFact\n"
        blueprint += "- LEFT JOIN the remaining dimension tables to get the descriptive name columns.\n"
        blueprint += "- Calculate Derived Formulas here (math without native aggregations).\n\n"
        
    else:
        blueprint += "ARCHITECTURE: TWO-TIER CTE\n\n"
        
        blueprint += "1. TIER 1 CTE ('AggregatedFact'):\n"
        blueprint += f"- FROM {primary_fact}\n"
        blueprint += "- JOIN all dimension tables.\n"
        blueprint += "- Apply FILTERS here (WHERE clause).\n"
        blueprint += "- GROUP BY the requested dimensions (both ID and Name columns).\n"
        blueprint += "- Apply Base Aggregations here.\n"
        blueprint += "- Evaluate formulas containing native aggregations here.\n\n"
        
        blueprint += "2. TIER 2 (OUTER QUERY):\n"
        blueprint += "- FROM AggregatedFact\n"
        blueprint += "- Calculate Derived Formulas here (math without native aggregations).\n\n"

    blueprint += "=== UNIVERSAL MAPPINGS ===\n\n"
    blueprint += "4. FILTERS TO APPLY:\n"
    if intent.get("raw_filters"):
        for raw_f in intent.get("raw_filters", []):
            for cond in raw_f.get("and", []):
                lvl = cond.get("dimensionLevelColumnName")
                vals = cond.get("values", [])
                if lvl in filters:
                    val_str = ", ".join([f"'{v}'" if isinstance(v, str) else str(v) for v in vals])
                    blueprint += f"- {filters[lvl]} IN ({val_str})\n"
    else:
        blueprint += "- NO FILTERS REQUESTED.\n"

    blueprint += "\n5. JOINS PATHS TO USE:\n"
    for dim, path in joins.items():
        for step in path:
            blueprint += f"- JOIN {step['table']} ON {step['on']}\n"

    blueprint += "\n6. DIMENSIONS TO GROUP BY:\n"
    dims = intent.get("dimensions", [])
    if dims:
        for dim in dims:
            blueprint += f"- {dim}\n"
    else:
        blueprint += "- NO DIMENSIONS REQUESTED. Do not group by anything.\n"

    blueprint += "\n7. MEASURES (STRICT MAPPING):\n"
    for m, data in semantics.items():
        formula = data.get("formula", "")
        phys_cols = data.get("physical_columns", [])
        
        if "CASE WHEN" in formula.upper() and phys_cols and phys_cols[0].lower() not in formula.lower():
            formula = f"MAX({phys_cols[0]})"
            
        blueprint += f"- Measure: {m}\n"
        blueprint += f"  Formula: {formula}\n"
        blueprint += f"  Allowed Columns: {phys_cols}\n"
        blueprint += f"  Rule: Ensure columns used in the formula exist in Allowed Columns.\n\n"
        
    return blueprint

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
Follow the provided Blueprint EXACTLY.
If the Blueprint specifies a THREE-TIER CTE, strictly separate filtering, aggregation, and formatting as instructed.

UNIVERSAL RULES:
- NEVER use a naked `SELECT *`. Always use table aliases like `SELECT f.*` to prevent duplicate column errors from JOINs.
- NEVER wrap variables in SUM() or COUNT() in the outer query! They are already aggregated in the CTE.
- ALWAYS wrap the denominator of division operations in NULLIF(..., 0) to prevent Divide by Zero errors.
- Ensure all column prefixes perfectly match the table aliases or names used in the FROM/JOIN clauses.

DO NOT hallucinate. Respond ONLY with the raw SQL query enclosed in ```sql ... ``` block."""

    blueprint = generate_blueprint(intent, semantics, joins, filters)
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Here is your exact SQL Blueprint:\n\n{blueprint}\n\nWrite the SQL query.")
    ]
    
    conn = get_db_connection()
    cursor = get_dict_cursor(conn) if not isinstance(conn, str) else None

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
            
            healing_prompt = f"The database threw this error:\n{error_msg}\n\nReview the Blueprint rules. "
            if "Unknown column" in error_msg:
                healing_prompt += "You either forgot to select this column using `f.*` in the first CTE, or you used a completely hallucinated column name. Look at the 'Allowed Columns' and fix it."
            elif "aggregate function" in error_msg or "GROUP BY" in error_msg:
                healing_prompt += "You likely put a SUM() or COUNT() in the outer query, or forgot to add a selected dimension to the GROUP BY."
            elif "Duplicate column" in error_msg:
                healing_prompt += "You used `SELECT *` which caused overlapping columns. Use `SELECT f.*` to only pull columns from the fact table."
                
            healing_prompt += " CRITICAL: NEVER delete a requested dimension, measure, or JOIN to 'fix' an error. You must find the correct column name (like changing '_desc' to '_name') or fix the alias instead of removing it. Fix the query and return ONLY the corrected SQL block."
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
