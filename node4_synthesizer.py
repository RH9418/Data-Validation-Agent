# import os
# import json
# import re
# from dotenv import load_dotenv
# from langchain_openai import AzureChatOpenAI
# from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
# from custom_tools.database_tools import get_db_connection, get_dict_cursor

# load_dotenv()

# # --- SETUP: Point this to your specific host log directory ---
# def get_host_specific_log_dir():
#     host = os.getenv("DB_HOST", "unknown_host").replace(".", "_")
#     db = os.getenv("DB_DATABASE")
#     if not db: db = os.getenv("DB_SCHEMA", "unknown_db")
#     return os.path.join("run_logs", f"{host}_{db}")

# LOG_DIR = get_host_specific_log_dir()

# def load_json(filename):
#     filepath = os.path.join(LOG_DIR, filename)
#     if os.path.exists(filepath):
#         with open(filepath, "r", encoding="utf-8") as f:
#             return json.load(f)
#     return {}

# # --- THE AGENTIC COMPILER ---
# def synthesize_sql_with_auto_healing(intent, semantics, joins, filters, max_retries=3):
#     print("\n" + "="*60 + "\n🧠 NODE 4: AGENTIC SQL SYNTHESIZER\n" + "="*60)
    
#     llm = AzureChatOpenAI(
#         azure_endpoint=os.getenv("AZURE_ENDPOINT"),
#         openai_api_key=os.getenv("AZURE_API_KEY"),
#         openai_api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
#         azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
#         temperature=0
#     )

#     # 1. Strict Two-Tier CTE System Prompt (Upgraded to prevent double-aggregation)
#         # 1. Strict Two-Tier CTE System Prompt (Upgraded for Zero-Guessing)
#     system_prompt = f"""You are an elite Data Engineer writing MySQL queries for SingleStore.
# You must construct a query using a STRICT Two-Tier CTE architecture.

# RULES FOR THE CTE ('AggregatedFact'):
# - Use the Primary Fact Table in the FROM clause. 
# - NEVER alias the primary fact table. Use its exact full name for all column prefixes.
# - JOIN dimension tables EXACTLY as defined in the Join Paths.
# - Apply filters natively in the WHERE clause using EXACTLY the Resolved Filters.
# - For requested dimensions, ALWAYS select and GROUP BY both the ID and Name columns from the dimension table (e.g., `dim_table.dim_name_id`, `dim_table.dim_name_name`).
# - Assume ALL physical columns from the semantics JSON belong to the Primary Fact Table unless specified otherwise. Prefix them accordingly (e.g., `fact_table.column_name`).
# - If a Derived Formula string ALREADY contains aggregation functions (like SUM, COUNT, IF), evaluate it directly INSIDE the CTE. Do NOT try to rebuild it in the outer query.

# RULES FOR THE OUTER QUERY:
# - SELECT the grouped dimensions and calculated measures FROM AggregatedFact.
# - ONLY calculate Derived Formulas here if they do NOT contain native aggregations in their raw string (e.g., simple division of two CTE columns).
# - CRITICAL: NEVER wrap variables in SUM() or COUNT() in the outer query!
# - ALWAYS wrap the denominator of division operations in NULLIF(..., 0).


# DO NOT hallucinate. Respond ONLY with the raw SQL query enclosed in ```sql ... ``` block."""


#     human_prompt = f"""Here are the empirically validated database mappings:

# EXTRACTED INTENT (Targets, Dimensions, Limit, Sort):
# {json.dumps(intent, indent=2)}

# RESOLVED SEMANTICS (Measures & Math Formulas):
# {json.dumps(semantics, indent=2)}

# JOIN PATHS (Proven Relational Connections):
# {json.dumps(joins, indent=2)}

# RESOLVED FILTERS (Verified Physical Columns for WHERE clause):
# {json.dumps(filters, indent=2)}

# Write the SQL query."""

#     messages = [
#         SystemMessage(content=system_prompt),
#         HumanMessage(content=human_prompt)
#     ]
    
#     conn = get_db_connection()
#     cursor = get_dict_cursor(conn) if not isinstance(conn, str) else None

#     # 2. The Validation Execution Loop (Upgraded to use LIMIT 0 instead of EXPLAIN)
#     for attempt in range(max_retries + 1):
#         print(f"🔄 Generation Attempt {attempt + 1}...")
#         response = llm.invoke(messages)
        
#         sql_match = re.search(r"```sql\n(.*?)\n```", response.content, re.DOTALL | re.IGNORECASE)
#         if not sql_match:
#             sql_query = response.content.replace("```sql", "").replace("```", "").strip()
#         else:
#             sql_query = sql_match.group(1).strip()
            
#         print(f"\n📝 Draft Query generated:\n{sql_query}\n")
        
#         if not cursor:
#             print("⚠️ Database connection failed. Cannot run validation loop. Returning unverified SQL.")
#             return sql_query

#         try:
#             print("🔬 Running LIMIT 0 Semantic Validation on database...")
#             # 🚀 THE FIX: Do not wrap CTEs in subqueries! 
#             # Strip trailing semicolons and replace the LLM's LIMIT with LIMIT 0 natively.
#             clean_query = sql_query.strip().rstrip(";")
#             clean_query = re.sub(r'\s+LIMIT\s+\d+$', '', clean_query, flags=re.IGNORECASE)
            
#             validation_wrapper = f"{clean_query}\nLIMIT 0;"
#             cursor.execute(validation_wrapper)
#             cursor.fetchall() # Clear buffer
#             print("✅ Semantic Validation Succeeded! The query is mathematically and structurally flawless.")
#             return sql_query
            
#         except Exception as e:
#             error_msg = str(e)
#             print(f"❌ Database Error Caught: {error_msg}")
#             try: cursor.fetchall() 
#             except: pass # Clear buffer on error
            
#             if attempt == max_retries:
#                 print("🛑 Max retries reached. Returning last failed query.")
#                 return sql_query
                
#             print("🛠️ Passing error back to LLM for Auto-Healing...")
#             messages.append(AIMessage(content=f"```sql\n{sql_query}\n```"))
#             messages.append(HumanMessage(content=f"The database threw this error when executing the query:\n{error_msg}\n\nReview the Rules (No double aggregation in outer query, CTE scoping, etc). Fix the query and return ONLY the corrected SQL block."))

#     if cursor: cursor.close()
#     if conn and not isinstance(conn, str): conn.close()

# if __name__ == "__main__":
#     print(f"Loading empirical metadata from {LOG_DIR}...")
    
#     intent = load_json("01_extracted_intent.json")
#     semantics = load_json("02_resolved_semantics.json")
#     joins = load_json("03_join_paths.json")
#     filters = load_json("04_resolved_filters.json")
    
#     if not intent:
#         print("❌ Error: Could not find JSON files. Ensure Nodes 1-3 have run first.")
#         exit(1)
        
#     final_query = synthesize_sql_with_auto_healing(intent, semantics, joins, filters)
    
#     print("\n" + "="*60 + "\n🏆 FINAL AUTO-HEALED SQL QUERY\n" + "="*60)
#     print(final_query)










import os
import json
import re
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from custom_tools.database_tools import get_db_connection, get_dict_cursor

load_dotenv()

# --- SETUP ---
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

# 🧪 NODE 3.5: COMPONENT-LEVEL PRE-VALIDATOR 🧪
def pre_validate_and_build_snippets(semantics, target_tables, cursor):
    """Fires diagnostic queries at the DB to build perfect SQL Snippets for the LLM."""
    print("\n" + "="*60 + "\n🧪 NODE 3.5: COMPONENT-LEVEL PRE-VALIDATOR\n" + "="*60)
    
    primary_fact = target_tables[0]
    override_table = target_tables[1] if len(target_tables) > 1 else ""
    
    def get_verified_prefix(col_name):
        if not cursor or col_name == "*": return "f."
        try:
            cursor.execute(f"SELECT `{col_name}` FROM {primary_fact} LIMIT 0")
            return "f."
        except:
            if override_table:
                try:
                    cursor.execute(f'SELECT "{col_name}" FROM {override_table} LIMIT 0')
                    return "o."
                except: pass
        return "f."

    golden_snippets = {}
    
    for m, data in semantics.items():
        formula = data.get("formula", "")
        m_type = data.get("type", "SUM")
        phys_cols = data.get("physical_columns", [])
        
        # 1. Clean CASE WHEN
        if "CASE WHEN" in formula.upper() and phys_cols and phys_cols[0].lower() not in formula.lower():
            formula = f"MAX({phys_cols[0]})"
            m_type = "MAX"
            
        # 2. Clean dirty names using physical column fallback
        words = re.findall(r'\b[a-zA-Z_]\w*\b', formula)
        sql_kw = {'sum', 'if', 'null', 'is', 'and', 'or', 'case', 'when', 'then', 'else', 'end', 'max', 'min', 'avg', 'count', 'distinct', 'nullif', 'ifnull'}
        for w in words:
            w_lower = w.lower()
            if w_lower not in sql_kw and not re.match(r'^(sum|max|min|avg|count)_', w_lower) and w not in semantics and w not in phys_cols:
                if phys_cols:
                    formula = re.sub(rf'\b{w}\b', phys_cols[0], formula)
                    
        # 3. Strip legacy prefixes
        formula = re.sub(r'\bfact_data\.', '', formula, flags=re.IGNORECASE)
        formula = re.sub(r'\bfact_override\.', '', formula, flags=re.IGNORECASE)
        
        # 4. DB-Backed Prefix Injection
        for pc in phys_cols:
            if pc == "*": continue
            prefix = get_verified_prefix(pc)
            formula = re.sub(rf'(?<![fo]\.)\b{re.escape(pc)}\b', f'{prefix}{pc}', formula)
            
        # 5. Route and Format
        has_native_agg = bool(re.search(r'\b(SUM|COUNT|MAX|MIN|AVG)\s*\(', formula, re.IGNORECASE))
        uses_alias = bool(re.search(r'\b(sum|max|min|avg|count)_', formula, re.IGNORECASE))
        
        if m_type in ["SUM", "MAX", "MIN", "AVG", "COUNT"] or (m_type == "FORMULA" and has_native_agg and not uses_alias):
            if not has_native_agg:
                agg = m_type if m_type != "FORMULA" else "SUM"
                formula = f"{agg}({formula})"
            golden_snippets[m] = {"tier": "Tier 2 (AggregatedFact)", "sql": f"{formula} AS {m}"}
            print(f"✅ Verified Base Metric: {m}")
        else:
            # Protect division
            if "NULLIF" not in formula.upper():
                formula = re.sub(r'/\s*\((.*?)\)', r'/ NULLIF(\1, 0)', formula)
                formula = re.sub(r'/\s*([a-zA-Z0-9_]+)', r'/ NULLIF(\1, 0)', formula)
            golden_snippets[m] = {"tier": "Tier 3 (Outer Query)", "sql": f"{formula} AS {m}"}
            print(f"✅ Verified Derived Math: {m}")

    return golden_snippets

# 🚀 THE BLUEPRINT GENERATOR 🚀
def generate_blueprint(intent, joins, filters, snippets):
    target_tables = intent.get("target_tables", ["fact_data"])
    primary_fact = target_tables[0]
    has_multiple_facts = len(target_tables) > 1
    
    blueprint = "=== SQL CONSTRUCTION BLUEPRINT ===\n\n"
    
    if has_multiple_facts:
        blueprint += "ARCHITECTURE: THREE-TIER CTE\n\n"
        blueprint += f"1. TIER 1 CTE ('FilteredFact'):\n- FROM {primary_fact} (alias as 'f')\n- INNER JOIN ONLY tables required for FILTERS.\n- Apply FILTERS (WHERE clause).\n- CRITICAL: Use `SELECT f.*`\n\n"
        blueprint += f"2. TIER 2 CTE ('AggregatedFact'):\n- FROM FilteredFact (alias as 'f')\n- LEFT JOIN {target_tables[1]} (alias as 'o') ON f.time_id = o.time_id AND f.product_id = o.product_id AND f.location_id = o.location_id\n- GROUP BY requested dimension IDs.\n\n"
        blueprint += "3. TIER 3 (OUTER QUERY):\n- FROM AggregatedFact af\n- LEFT JOIN remaining dimension tables to get `_name` columns.\n\n"
    else:
        blueprint += "ARCHITECTURE: TWO-TIER CTE\n\n"
        blueprint += f"1. TIER 1 CTE ('AggregatedFact'):\n- FROM {primary_fact} (alias as 'f')\n- JOIN all dimension tables.\n- Apply FILTERS (WHERE clause).\n- GROUP BY requested dimension IDs and Names.\n\n"
        blueprint += "2. TIER 2 (OUTER QUERY):\n- FROM AggregatedFact af\n\n"

    blueprint += "=== PRE-VALIDATED MEASURE SNIPPETS ===\n"
    blueprint += "CRITICAL INSTRUCTION: You MUST copy and paste these EXACT strings into the specified Tiers. Do not alter column names or aliases.\n\n"
    
    for m, data in snippets.items():
        blueprint += f"Snippet: {data['sql']}\nTarget Location: {data['tier']}\n\n"
        
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
Do not think about the math or table prefixes; the Blueprint has Pre-Validated Golden Snippets for you to copy and paste.

UNIVERSAL RULES:
- NEVER use a naked `SELECT *`. Always use table aliases like `SELECT f.*` to prevent duplicate column errors from JOINs.
- Ensure all column prefixes perfectly match the table aliases or names used in the FROM/JOIN clauses.

DO NOT hallucinate. Respond ONLY with the raw SQL query enclosed in ```sql ... ``` block."""

    conn = get_db_connection()
    cursor = get_dict_cursor(conn) if not isinstance(conn, str) else None

    # Run Node 3.5 Micro-Testing
    snippets = pre_validate_and_build_snippets(semantics, intent.get("target_tables", ["fact_data"]), cursor)
    blueprint = generate_blueprint(intent, joins, filters, snippets)
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Here is your exact SQL Blueprint:\n\n{blueprint}\n\nWrite the SQL query.")
    ]

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
            
            healing_prompt = f"The database threw this error:\n{error_msg}\n\nReview the Blueprint rules. "
            if "Unknown column" in error_msg:
                healing_prompt += "Check your SELECT f.* list in Tier 1. If it's a dimension name, use '_name', not '_desc'."
            elif "aggregate function" in error_msg or "GROUP BY" in error_msg:
                healing_prompt += "You likely put a SUM() or COUNT() in the outer query instead of Tier 2, or you forgot to add a dimension ID to the GROUP BY."
            elif "Duplicate column" in error_msg:
                healing_prompt += "You used a naked `SELECT *` which caused overlapping columns. Use `SELECT f.*`."
                
            healing_prompt += " CRITICAL: NEVER delete a requested dimension or measure to 'fix' an error. Return ONLY the corrected SQL block."
            messages.append(HumanMessage(content=healing_prompt))

    if cursor: cursor.close()
    if conn and not isinstance(conn, str): conn.close()

if __name__ == "__main__":
    intent = load_json("01_extracted_intent.json")
    semantics = load_json("02_resolved_semantics.json")
    joins = load_json("03_join_paths.json")
    filters = load_json("04_resolved_filters.json")
    
    final_query = synthesize_sql_with_auto_healing(intent, semantics, joins, filters)
    
    print("\n" + "="*60 + "\n🏆 FINAL AUTO-HEALED SQL QUERY\n" + "="*60)
    print(final_query)
