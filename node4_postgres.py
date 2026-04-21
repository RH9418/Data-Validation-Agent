import os
import json
import re
import sqlglot
from dotenv import load_dotenv
from custom_tools.database_tools import get_db_connection, get_dict_cursor

load_dotenv()

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

def deterministic_postgres_compiler(intent, semantics, joins, filters):
    print("\n" + "="*60 + "\n NODE 4: DETERMINISTIC POSTGRES COMPILER\n" + "="*60)
    
    conn = get_db_connection()
    cursor = get_dict_cursor(conn) if not isinstance(conn, str) else None
    db_schema = os.getenv("DB_SCHEMA", "public")
    
    if cursor:
        try: 
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
        except Exception: 
            if conn: conn.rollback()

    target_tables = intent.get("target_tables", ["fact_data"])
    primary_fact = target_tables[0]
    has_override = len(target_tables) > 1
    override_table = target_tables[1] if has_override else ""

    def get_verified_prefix(col_name):
        if not cursor or col_name == "*": return "f."
        try:
            cursor.execute(f'SELECT "{col_name}" FROM {db_schema}.{primary_fact} LIMIT 0')
            return "f."
        except Exception:
            if conn: conn.rollback()
            if override_table:
                try:
                    cursor.execute(f'SELECT "{col_name}" FROM {db_schema}.{override_table} LIMIT 0')
                    return "o."
                except Exception: 
                    if conn: conn.rollback()
            return "f."

    base_aggs = set()
    derived_math = []
    defined_aliases = set()
    all_measures = set(semantics.keys())
    
    # --- PASS 1: PARSE MEASURES ---
    for measure_name, data in semantics.items():
        formula = data.get("formula", "")
        m_type = data.get("type", "SUM")
        phys_cols = data.get("physical_columns", [])
        
        # Transpile MySQL functions to Postgres (IF -> CASE WHEN, IFNULL -> COALESCE)
        try:
            formula = sqlglot.transpile(formula, read='mysql', write='postgres')[0]
        except Exception: pass

        if "CASE WHEN" in formula.upper() and phys_cols and phys_cols[0].lower() not in formula.lower():
            formula = f"MAX({phys_cols[0]})"
            m_type = "MAX"

        for phys_col in phys_cols:
            if phys_col not in formula and phys_col != "*":
                words = re.findall(r'\b[a-zA-Z_]\w*\b', formula)
                sql_kw = {'sum', 'if', 'null', 'is', 'and', 'or', 'case', 'when', 'then', 'else', 'end', 'max', 'min', 'avg', 'count', 'distinct', 'nullif', 'coalesce'}
                for w in words:
                    w_lower = w.lower()
                    is_agg_prefix = bool(re.match(r'^(sum|max|min|avg|count)_', w_lower))
                    if w_lower not in sql_kw and not is_agg_prefix and w not in all_measures and w not in phys_cols:
                        formula = re.sub(rf'\b{w}\b', phys_col, formula)
                        break

        formula = re.sub(r'\bfact_data\.', '', formula, flags=re.IGNORECASE)
        formula = re.sub(r'\bfact_override\.', '', formula, flags=re.IGNORECASE)

        safe_formula = formula
        for col in phys_cols:
            if col == "*": continue
            prefix = get_verified_prefix(col)
            safe_formula = re.sub(rf'(?<![fo]\.)\b{re.escape(col)}\b', f'{prefix}{col}', safe_formula)

        has_native_agg = bool(re.search(r'\b(SUM|COUNT|MAX|MIN|AVG)\s*\(', safe_formula, re.IGNORECASE))
        uses_alias = bool(re.search(r'\b(sum|max|min|avg|count)_', safe_formula, re.IGNORECASE))

        if m_type in ["SUM", "MAX", "MIN", "AVG", "COUNT"] or (m_type == "FORMULA" and has_native_agg and not uses_alias):
            if not has_native_agg:
                agg = m_type if m_type != "FORMULA" else "SUM"
                safe_formula = f"{agg}({safe_formula})"
            base_aggs.add(f"        {safe_formula} AS {measure_name}")
            defined_aliases.add(measure_name)
        else:
            safe_math = safe_formula
            for col in phys_cols:
                if col == "*": continue
                safe_math = re.sub(rf'(?<![a-zA-Z0-9_\.])\b{re.escape(col)}\b(?!_)', f'sum_{col}', safe_math)

            safe_math = re.sub(r'SUM\((?:[fo]\.)?([a-zA-Z0-9_]+)\)', r'sum_\1', safe_math, flags=re.IGNORECASE)
            safe_math = re.sub(r'MAX\((?:[fo]\.)?([a-zA-Z0-9_]+)\)', r'max_\1', safe_math, flags=re.IGNORECASE)
            safe_math = re.sub(r'AVG\((?:[fo]\.)?([a-zA-Z0-9_]+)\)', r'avg_\1', safe_math, flags=re.IGNORECASE)

            if "NULLIF" not in safe_math.upper():
                safe_math = re.sub(r'/\s*\((.*?)\)', r'/ NULLIF(\1, 0)', safe_math)
                safe_math = re.sub(r'/\s*([a-zA-Z0-9_]+)', r'/ NULLIF(\1, 0)', safe_math)
            
            derived_math.append(f"        {safe_math} AS {measure_name}")

    # --- PASS 2: MISSING DEPENDENCIES ---
    for math_str in derived_math:
        math_only = math_str.split(" AS ")[0]
        matches = re.findall(r'\b(sum|max|min|avg|count)_([a-zA-Z0-9_]+)\b', math_only, re.IGNORECASE)
        for agg_type, col in matches:
            alias = f"{agg_type.lower()}_{col}"
            if alias not in defined_aliases:
                prefix = get_verified_prefix(col)
                base_aggs.add(f"        {agg_type.upper()}({prefix}{col}) AS {alias}")
                defined_aliases.add(alias)

    # --- 3. BUILD UNIVERSAL SQL ---
    tier1_selects = ["f.*"]
    tier1_joins = []
    joined_tables = set()

    for dim in intent.get("filter_columns", []):
        if dim in joins:
            for step in joins[dim]:
                t = step["table"]
                if t not in joined_tables:
                    on_c = step["on"].replace(f"{primary_fact}.", "f.")
                    tier1_joins.append(f"    INNER JOIN {db_schema}.{t} ON {on_c}")
                    joined_tables.add(t)

    dim_fact_keys = {}
    group_keys = []
    for dim in intent.get("dimensions", []):
        dim_id = f"{dim}_id"
        if dim in joins and joins[dim]:
            path = joins[dim]
            if len(path) == 1:
                group_keys.append(f"f.{dim_id}")
                dim_fact_keys[dim] = dim_id
            else:
                for step in path[:-1]:
                    t = step["table"]
                    if t not in joined_tables:
                        on_c = step["on"].replace(f"{primary_fact}.", "f.")
                        tier1_joins.append(f"    LEFT JOIN {db_schema}.{t} ON {on_c}")
                        joined_tables.add(t)
                last_xref = path[-2]["table"]
                tier1_selects.append(f"{last_xref}.{dim_id}")
                group_keys.append(f"f.{dim_id}")
                dim_fact_keys[dim] = dim_id
        else:
            group_keys.append(f"f.{dim_id}")
            dim_fact_keys[dim] = dim_id

    sql = "WITH FilteredFact AS (\n"
    sql += "    SELECT " + ", ".join(tier1_selects) + "\n"
    sql += f"    FROM {db_schema}.{primary_fact} f\n"
    if tier1_joins:
        sql += "\n".join(tier1_joins) + "\n"
        
    where_c = []
    for raw_f in intent.get("raw_filters", []):
        for cond in raw_f.get("and", []):
            lvl = cond.get("dimensionLevelColumnName")
            if lvl in filters:
                vals = ", ".join([f"'{v}'" if isinstance(v, str) else str(v) for v in cond.get("values", [])])
                where_c.append(f"        {filters[lvl]} IN ({vals})")
                
    if where_c:
        sql += "    WHERE\n" + " AND\n".join(where_c) + "\n"
    else:
        sql += "    WHERE 1=1\n"
        
    sql += "),\n"
    
    sql += "AggregatedFact AS (\n"
    sql += "    SELECT\n"
    
    if group_keys:
        group_keys = list(dict.fromkeys(group_keys))
        sql += ",\n".join([f"        {k}" for k in group_keys]) + ",\n"
        
    sql += ",\n".join(sorted(list(base_aggs))) + "\n"
    sql += "    FROM FilteredFact f\n"
    
    if override_table:
        sql += f"    LEFT JOIN {db_schema}.{override_table} o ON f.time_id = o.time_id AND f.product_id = o.product_id AND f.location_id = o.location_id\n"
        
    if group_keys:
        sql += "    GROUP BY\n"
        sql += ",\n".join([f"        {k}" for k in group_keys]) + "\n"
        
    sql += ")\n"
    
    sql += "SELECT\n"
    
    outer_selects = []
    for dim in intent.get("dimensions", []):
        if dim in joins:
            target_dim_t = joins[dim][-1]["table"]
            outer_selects.append(f"    af.{dim}_id")
            outer_selects.append(f"    {target_dim_t}.{dim}_name")
            
    if outer_selects:
        sql += ",\n".join(outer_selects) + ",\n"
        
    ordered_measures = []
    for m in intent.get("measures", []):
        if m in defined_aliases:
            ordered_measures.append(f"    af.{m}")
        else:
            for d in derived_math:
                if d.endswith(f" AS {m}"):
                    clean_d = d.replace("f.", "af.").replace("o.", "af.").strip()
                    ordered_measures.append(f"    {clean_d}")
                    
    sql += ",\n".join(ordered_measures) + "\n"
    sql += "FROM AggregatedFact af\n"
    
    joined_dims = set()
    for dim in intent.get("dimensions", []):
        if dim in joins and dim in dim_fact_keys:
            fact_col = dim_fact_keys[dim]
            prev_table = "af"
            for step in joins[dim]:
                t = step["table"]
                if t not in joined_dims:
                    on_c = step["on"]
                    match = re.search(rf'\b{primary_fact}\.([a-zA-Z0-9_]+)', on_c, re.IGNORECASE)
                    if match:
                        on_c = on_c.replace(match.group(0), f"{prev_table}.{match.group(1)}")
                    sql += f"LEFT JOIN {db_schema}.{t} ON {on_c}\n"
                    joined_dims.add(t)
                    
    if intent.get("dimensions"):
        first_dim = intent["dimensions"][0]
        if first_dim in joins:
            target_dim_t = joins[first_dim][-1]["table"]
            sql += f"ORDER BY {target_dim_t}.{first_dim}_name ASC\n"
            
    limit = intent.get("limit", 200)
    sql += f"LIMIT {limit};\n"

    if cursor: cursor.close()
    if conn and not isinstance(conn, str): conn.close()

    return sql

if __name__ == "__main__":
    intent = load_json("01_extracted_intent.json")
    semantics = load_json("02_resolved_semantics.json")
    joins = load_json("03_join_paths.json")
    filters = load_json("04_resolved_filters.json")
    
    if not intent:
        print("❌ Error: Could not find JSON files.")
        exit(1)
        
    final_query = deterministic_postgres_compiler(intent, semantics, joins, filters)
    
    print("\n" + "="*60 + "\n🏆 DETERMINISTIC POSTGRES QUERY\n" + "="*60)
    print(final_query)
