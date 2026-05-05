# import os
# from typing import TypedDict, Annotated, Sequence
# from dotenv import load_dotenv
# from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
# from langgraph.graph import StateGraph, END
# from langgraph.graph.message import add_messages
# from langgraph.prebuilt import ToolNode

# # Import the tools we built in Step 1
# from agent_tools import (
#     search_tables_by_keyword,
#     get_table_schema,
#     search_columns_by_keyword,
#     sample_column_data,
#     search_table_for_value # <-- Add the new tool here
# )


# # Set up Azure OpenAI Client
# from langchain_openai import AzureChatOpenAI

# # Load environment variables from .env file
# load_dotenv()

# # Initialize the Azure LLM using your specific credentials
# llm = AzureChatOpenAI(
#     azure_endpoint=os.getenv("AZURE_ENDPOINT"),
#     api_key=os.getenv("AZURE_API_KEY") or os.getenv("ZURE_API_KEY"), # Handling the typo just in case!
#     api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
#     azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
#     temperature=0
# )

# # 1. Define the tools
# tools = [
#     search_tables_by_keyword,
#     get_table_schema,
#     search_columns_by_keyword,
#     sample_column_data,
#     search_table_for_value
# ]

# # Bind tools to the LLM
# llm_with_tools = llm.bind_tools(tools)

# # 2. Define the Graph State
# class AgentState(TypedDict):
#     # The `add_messages` function appends new messages to the list, rather than overwriting
#     messages: Annotated[Sequence[BaseMessage], add_messages]

# # 3. Define the Agent Node
# def agent_node(state: AgentState):
#     print("\n🧠 [Agent is thinking...]")
#     response = llm_with_tools.invoke(state["messages"])
#     return {"messages": [response]}

# # 4. Define the Routing Logic
# def should_continue(state: AgentState):
#     messages = state["messages"]
#     last_message = messages[-1]
    
#     # If the LLM decided to call a tool, route to the "tools" node
#     if last_message.tool_calls:
#         tool_names = [tc['name'] for tc in last_message.tool_calls]
#         print(f"🛠️  [Agent decided to use tools: {', '.join(tool_names)}]")
#         return "continue"
    
#     # Otherwise, it has finished its task
#     print("✅ [Agent has reached a conclusion.]")
#     return "end"

# # 5. Build the LangGraph
# workflow = StateGraph(AgentState)

# # Add nodes
# workflow.add_node("agent", agent_node)
# workflow.add_node("tools", ToolNode(tools)) # ToolNode automatically executes the requested tools

# # Add edges
# workflow.set_entry_point("agent")
# workflow.add_conditional_edges(
#     "agent",
#     should_continue,
#     {
#         "continue": "tools",
#         "end": END
#     }
# )
# workflow.add_edge("tools", "agent") # After tools run, go back to the agent

# # Compile the graph
# app = workflow.compile()

# # --- SYSTEM PROMPT ---
# system_prompt = """
# You are an autonomous Database Schema Detective. Your job is to map logical UI terms 
# to their exact physical tables and columns, OR find their mathematical formulas.

# You have no prior knowledge of the database. You MUST use your tools to explore it.

# STRATEGY FOR FINDING METRICS:
# 1. First, use `search_columns_by_keyword` to look for physical columns matching the logical measure.
# 2. IF the column is NOT FOUND, it is likely a derived metric. 
# 3. To find derived metrics, search for mapping tables using `search_tables_by_keyword` (look for 'measure', 'map', 'agg').
# 4. Once you identify potential mapping tables, use `search_table_for_value` to search inside them for the missing logical term.
# 5. **CRITICAL RECURSIVE VERIFICATION:** If you find a formula (e.g., `COALESCE(col_a, col_b)`), you MUST verify where `col_a` and `col_b` physically live! Do not assume they are in the same table. Use `search_columns_by_keyword` on the columns inside the formula to find their exact physical tables.

# Your final output MUST list the physical `schema.table.column` for EVERY piece of data required, including the base columns inside any derived formulas.
# """



# if __name__ == "__main__":
#     # Test Scenario: Ask the agent to find where a specific field from your API payload lives.
#     test_question = "I need to construct a SQL query, but the API payload asks for 'sys_base_dollars_fcst' and 'actual_sales_and_roy_fcst_dollars'. Can you figure out exactly which tables and columns hold this data? Please show your reasoning."
    
#     print(f"User: {test_question}\n" + "-"*50)
    
#     initial_state = {
#         "messages": [
#             SystemMessage(content=system_prompt),
#             HumanMessage(content=test_question)
#         ]
#     }
    
#     # Stream the events as the agent works through the graph
#     for event in app.stream(initial_state, {"recursion_limit": 15}):
#         for key, value in event.items():
#             if key == "agent":
#                 # Print the final output if there are no tool calls
#                 if not value["messages"][-1].tool_calls:
#                     print("\n📝 FINAL ANSWER:\n")
#                     print(value["messages"][-1].content)
#             elif key == "tools":
#                 print(f"📊 [Tool execution complete. Returning database results to the agent...]")


















# import os
# import json
# from typing import TypedDict, Annotated, Sequence, List
# from dotenv import load_dotenv
# from pydantic import BaseModel, Field
# from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
# from langgraph.graph import StateGraph, END
# from langgraph.graph.message import add_messages
# from langgraph.prebuilt import ToolNode
# from langchain_core.messages import ToolMessage

# # Make sure you import the new trace_dimension_hierarchy tool!
# from agent_tools import (
#     trace_metric_to_physical,
#     trace_dimension_hierarchy, 
#     execute_read_query
# )

# from langchain_openai import AzureChatOpenAI

# load_dotenv()

# llm = AzureChatOpenAI(
#     azure_endpoint=os.getenv("AZURE_ENDPOINT"),
#     api_key=os.getenv("AZURE_API_KEY") or os.getenv("ZURE_API_KEY"),
#     api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
#     azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
#     temperature=0
# )

# # --- 1. Define the LOSSLESS Structured Output Schema ---
# class PhysicalDependency(BaseModel):
#     physical_table: str = Field(description="The exact physical table name")
#     physical_column: str = Field(description="The exact physical column name")

# class ColumnMapping(BaseModel):
#     logical_ui_name: str = Field(description="The original name requested by the API")
#     is_derived_formula: bool = Field(description="True if this is a derived metric using a formula")
#     formula: str = Field(default="", description="The exact formula string extracted by the tool. Empty if it's a raw physical column.")
#     dependencies: List[PhysicalDependency] = Field(description="List of all physical tables and columns required for this metric")

# class DimensionMapping(BaseModel):
#     logical_dimension: str = Field(description="The dimension name from the API")
#     physical_table: str = Field(description="The physical _dim_desc table")
#     primary_key: str = Field(description="The primary key column")
#     requires_bridge: bool = Field(description="True if an xref bridge table is needed")
#     bridge_table: str = Field(default="", description="The xref table name, if required")

# class SubmitFinalMapping(BaseModel):
#     """Call this tool when you have definitively found all physical mappings and are ready to submit your final answer."""
#     mappings: List[ColumnMapping]
#     dimension_mappings: List[DimensionMapping] = Field(default_factory=list)

# # --- 2. Graph State ---
# class AgentState(TypedDict):
#     messages: Annotated[Sequence[BaseMessage], add_messages]
#     final_mapping_json: dict  
#     validation_results: list  

# # --- 3. Agent Node ---
# tools = [trace_metric_to_physical, trace_dimension_hierarchy]
# llm_with_tools = llm.bind_tools(tools + [SubmitFinalMapping])

# def agent_node(state: AgentState):
#     print("\n🧠 [Agent is thinking...]")
#     response = llm_with_tools.invoke(state["messages"])
#     return {"messages": [response]}

# # --- 4. Deterministic Validation Node ---
# def validate_mapping_node(state: AgentState):
#     print("\n" + "="*50)
#     print("🔬 [DETERMINISTIC VALIDATION PHASE]")
#     print("="*50)
    
#     last_message = state["messages"][-1]
#     mappings = []
#     tool_call_id = None 
    
#     if last_message.tool_calls:
#         for tc in last_message.tool_calls:
#             if tc['name'] == 'SubmitFinalMapping':
#                 mappings = tc['args'].get("mappings", [])
#                 tool_call_id = tc['id'] 
#                 break
                
#     validation_log = []
#     db_schema = os.getenv("DB_SCHEMA", os.getenv("DB_DATABASE", "public"))
#     db_type = os.getenv("DB_TYPE", "postgres").lower()
#     q = '"' if db_type == "postgres" else '`' 
    
#     # Iterate through the mappings, and then through each dependency within the mapping
#     for mapping in mappings:
#         logical_name = mapping.get("logical_ui_name", "Unknown")
#         dependencies = mapping.get("dependencies", [])
        
#         for dep in dependencies:
#             table = dep.get("physical_table")
#             col = dep.get("physical_column")
            
#             # Don't validate "NOT_FOUND" placeholders
#             if table == "NOT_FOUND":
#                 continue
                
#             print(f"⏳ Validating: {table}.{col} (Dependency for UI metric '{logical_name}')...")
            
#             query = f"""
#                 SELECT {q}{col}{q} 
#                 FROM {q}{db_schema}{q}.{q}{table}{q} 
#                 LIMIT 1;
#             """
            
#             result = execute_read_query(query)
            
#             if result and "error" not in result[0]:
#                 if len(result) > 0:
#                     msg = f"✅ SUCCESS: {table}.{col} exists and contains data."
#                     print(msg)
#                     validation_log.append({"status": "pass", "message": msg, "mapping": mapping, "dep": dep})
#                 else:
#                     msg = f"❌ FAILED: {table}.{col} exists but is currently completely empty or 0."
#                     print(msg)
#                     validation_log.append({"status": "fail", "message": msg, "mapping": mapping, "dep": dep})
#             else:
#                 error_msg = result[0].get("error", "Unknown DB Error") if result else "No result returned"
#                 msg = f"❌ FAILED: {table}.{col} threw an error -> {error_msg}"
#                 print(msg)
#                 validation_log.append({"status": "fail", "message": msg, "mapping": mapping, "dep": dep})
            
#     # --- THE FEEDBACK LOOP ---
#     failed_mappings = [v for v in validation_log if v["status"] == "fail"]
    
#     if failed_mappings:
#         feedback = "The following dependencies failed database validation (table was empty or column didn't exist). Please choose an alternative table/column from your earlier tool results and submit the corrected mapping using SubmitFinalMapping:\n"
#         for fail in failed_mappings:
#             ui_name = fail['mapping']['logical_ui_name']
#             bad_table = fail['dep']['physical_table']
#             bad_col = fail['dep']['physical_column']
#             feedback += f"- UI Metric '{ui_name}': Dependency {bad_table}.{bad_col} failed validation.\n"
        
#         return {
#             "validation_results": validation_log, 
#             "final_mapping_json": tc['args'] if 'tc' in locals() else {"mappings": mappings},
#             "messages": [ToolMessage(content=feedback, tool_call_id=tool_call_id)]
#         }
#     else:
#         return {
#             "validation_results": validation_log, 
#             "final_mapping_json": tc['args'] if 'tc' in locals() else {"mappings": mappings},
#             "messages": [ToolMessage(content="✅ All validations passed!", tool_call_id=tool_call_id)]
#         }

# # --- 5. Routing Logic ---
# def route_agent_action(state: AgentState):
#     last_message = state["messages"][-1]
    
#     if last_message.tool_calls:
#         for tool_call in last_message.tool_calls:
#             if tool_call['name'] == 'SubmitFinalMapping':
#                 print("\n🎯 [Agent submitted final mapping. Routing to Validator...]")
#                 return "validate"
        
#         tool_names = [tc['name'] for tc in last_message.tool_calls]
#         print(f"🛠️  [Agent decided to use exploration tools: {', '.join(tool_names)}]")
#         return "tools"
    
#     return "end"

# def route_after_validation(state: AgentState):
#     last_message = state["messages"][-1].content
#     if "failed database validation" in last_message:
#         print("\n🔄 [Validation failed! Sending feedback back to Agent for correction...]")
#         return "agent"
    
#     print("\n🎉 [All mappings validated successfully! Ready for SQL Architect]")
#     return "end"

# # --- 6. Build the LangGraph ---
# workflow = StateGraph(AgentState)
# workflow.add_node("agent", agent_node)
# workflow.add_node("tools", ToolNode(tools))
# workflow.add_node("validate", validate_mapping_node)

# workflow.set_entry_point("agent")
# workflow.add_conditional_edges("agent", route_agent_action, {"tools": "tools", "validate": "validate", "end": END})
# workflow.add_edge("tools", "agent")
# workflow.add_conditional_edges("validate", route_after_validation, {"agent": "agent", "end": END})

# app = workflow.compile()

# # --- SYSTEM PROMPT ---
# system_prompt = """
# You are an autonomous Database Schema Detective. Your job is to map logical UI terms to physical tables.

# STRATEGY FOR METRICS:
# 1. For every logical metric the API requests, call `trace_metric_to_physical`.
# 2. The tool will output the EXACT formula (if it's derived) and the physical dependencies.
# 3. CRITICAL: You MUST map EVERY dependency returned by the tool into the `dependencies` array. Include the `formula` string exactly as provided by the tool. DO NOT guess or change table names.

# STRATEGY FOR DIMENSIONS:
# 4. For every dimension requested in the payload, call the `trace_dimension_hierarchy` tool to find its exact table, primary key, and bridge details.

# WHEN YOU ARE DONE:
# Call `SubmitFinalMapping`. 
# - Include ALL metric mappings (with their formulas and nested dependencies arrays) and ALL dimension mappings.
# - If the tool discovered a bridge table for a dimension, ensure `requires_bridge` is true and note the table name.

# SELF-HEALING & CORRECTION:
# - If a metric returns 'FAILED_BUT_SUGGESTS', choose the most logically similar column from the `suggestions` array.
# - If the system replies that a mapping "failed database validation", look at the alternative tables returned by your earlier tool calls, choose a DIFFERENT physical table for that specific dependency, and call `SubmitFinalMapping` again until it passes.
# """

# # --- EXECUTION ---
# if __name__ == "__main__":
#     payload_file = "payload.json" 
    
#     if not os.path.exists(payload_file):
#         print(f"❌ Error: {payload_file} not found!")
#         exit(1)
        
#     with open(payload_file, "r") as f:
#         payload = json.load(f)
        
#     query_block = payload.get("variables", {}).get("query", {})
#     measures = query_block.get("aggregatedMeasures", [])
#     dimensions = query_block.get("dimensionLevels", [])
    
#     # Also extract dimensions from the scope (filters)
#     filters = query_block.get("scope", {}).get("dimensionFilters", []) if query_block.get("scope") else []
#     filter_dims = [f.get("dimensionColumnName") for f in filters if f.get("dimensionColumnName")]
#     all_dimensions = list(set(dimensions + filter_dims))
    
#     target_tables = query_block.get("datatable", [])
    
#     measures_str = "', '".join(measures) if measures else "None"
#     dimensions_str = "', '".join(all_dimensions) if all_dimensions else "None"
#     tables_str = "', '".join(target_tables)
    
#     test_question = f"""
#     I need to construct a SQL query based on an API payload. 
#     The API payload is asking for data from these target tables: '{tables_str}'.
    
#     It is requesting these specific logical measures: '{measures_str}'.
#     It is requesting these dimensions (Group Bys and Filters): '{dimensions_str}'.
    
#     Can you figure out exactly which tables, columns, and bridge tables hold the data for these?
#     """
    
#     print(f"User: {test_question}\n" + "-"*50)
    
#     initial_state = {
#         "messages": [SystemMessage(content=system_prompt), HumanMessage(content=test_question)],
#         "final_mapping_json": {},
#         "validation_results": []
#     }
    
#     final_state = None
#     for event in app.stream(initial_state, {"recursion_limit": 25}):
#         if "validate" in event:
#             final_state = event["validate"]
            
#     # --- SAVE TO JSON FOR THE SQL ARCHITECT ---
#     if final_state and "final_mapping_json" in final_state:
#         output_file = "validated_schema_map.json"
        
#         final_output = {
#             "api_payload_context": {
#                 "measures": measures,
#                 "dimensions": dimensions,
#                 "filters": query_block.get("scope", {})
#             },
#             "validated_mappings": final_state["final_mapping_json"]
#         }
        
#         with open(output_file, "w", encoding="utf-8") as f:
#             json.dump(final_output, f, indent=4)
            
#         print("\n" + "="*80)
#         print(f"🎉 SUCCESS! Validated Schema Map saved to: {output_file}")
#         print("="*80)
#     else:
#         print("\n❌ Execution failed or did not reach the validation phase.")













import os
import json
from typing import TypedDict, Annotated, Sequence, List
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import ToolMessage
from typing import Literal

from agent_tools import (
    trace_metric_to_physical,
    trace_dimension_hierarchy, 
    execute_read_query
)

from langchain_openai import AzureChatOpenAI

load_dotenv()

llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_ENDPOINT"),
    api_key=os.getenv("AZURE_API_KEY") or os.getenv("ZURE_API_KEY"),
    api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
    azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
    temperature=0
)

# --- 1. Define the LOSSLESS Structured Output Schema ---
class PhysicalDependency(BaseModel):
    physical_table: str = Field(description="The exact physical table name")
    physical_column: str = Field(description="The exact physical column name")

class ColumnMapping(BaseModel):
    logical_ui_name: str = Field(description="The original name requested by the API")
    is_derived_formula: bool = Field(description="True if this is a derived metric using a formula")
    formula: str = Field(default="", description="The exact formula string extracted by the tool. Empty if it's a raw physical column.")
    dependencies: List[PhysicalDependency] = Field(description="List of all physical tables and columns required for this metric")
    required_intermediate_aliases: List[str] = Field(
    description="CRITICAL: You MUST list any aggregated aliases (like 'sum_editable', 'avg_sys_attendance') found inside the formula. If none, return an empty array []"
)

    metric_stage: Literal["BASE_ONLY", "STANDARD_AGGREGATION", "MATH_ON_MATH"] = Field(
        description="""
        BASE_ONLY: A raw physical column.
        STANDARD_AGGREGATION: Uses a standard prefix (sum_, avg_, max_) or logic acting purely on physical columns.
        MATH_ON_MATH: A complex ratio or derived formula referencing other aggregated aliases.
        """
    )

class DimensionMapping(BaseModel):
    logical_dimension: str = Field(description="The dimension name from the API")
    physical_table: str = Field(description="The physical _dim_desc table")
    primary_key: str = Field(description="The primary key column")
    requires_bridge: bool = Field(description="True if an xref bridge table is needed")
    bridge_table: str = Field(default="", description="The xref table name, if required")
    base_key: str = Field(
        default="", 
        description="The column in fact_data used to join the bridge or dimension (e.g., product_id, time_id)."
    )
    display_column: str = Field(
        default="", 
        description="The descriptive name column in the physical dimension table that Agent 1 should SELECT (e.g., product4_name, month_name)."
    )

class SubmitFinalMapping(BaseModel):
    """Call this tool when you have definitively found all physical mappings and are ready to submit your final answer."""
    mappings: List[ColumnMapping]
    dimension_mappings: List[DimensionMapping] = Field(default_factory=list)

# --- 2. Graph State ---
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    final_mapping_json: dict  
    validation_results: list  

# --- 3. Agent Node ---
tools = [trace_metric_to_physical, trace_dimension_hierarchy]
llm_with_tools = llm.bind_tools(tools + [SubmitFinalMapping])

def agent_node(state: AgentState):
    print("\n🧠 [Agent is thinking...]")
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}

# --- 4. Deterministic Validation Node ---
def validate_mapping_node(state: AgentState):
    print("\n" + "="*50)
    print("🔬 [DETERMINISTIC VALIDATION PHASE]")
    print("="*50)
    
    last_message = state["messages"][-1]
    mappings = []
    tool_call_id = None
    
    if last_message.tool_calls:
        for tc in last_message.tool_calls:
            if tc['name'] == 'SubmitFinalMapping':
                mappings = tc['args'].get("mappings", [])
                tool_call_id = tc['id']
                break
                
    validation_log = []
    db_schema = os.getenv("DB_SCHEMA", os.getenv("DB_DATABASE", "public"))
    db_type = os.getenv("DB_TYPE", "postgres").lower()
    q = '"' if db_type == "postgres" else '`'
    
    # Iterate through the mappings, and then through each dependency within the mapping
    for mapping in mappings:
        logical_name = mapping.get("logical_ui_name", "Unknown")
        dependencies = mapping.get("dependencies", [])
        
        for dep in dependencies:
            table = dep.get("physical_table")
            col = dep.get("physical_column")
            
            # Don't validate "NOT_FOUND" placeholders
            if table == "NOT_FOUND":
                continue
                
            print(f"⏳ Validating: {table}.{col} (Dependency for UI metric '{logical_name}')...")
            
            # FIX: Removed the != 0 and IS NOT NULL checks to prevent Postgres crashes on strings/dates
            query = f"""
                SELECT {q}{col}{q}
                FROM {q}{db_schema}{q}.{q}{table}{q}
                LIMIT 1;
            """
            
            result = execute_read_query(query)
            
            if result and "error" not in result[0]:
                msg = f"✅ SUCCESS: {table}.{col} exists and contains data."
                print(msg)
                validation_log.append({"status": "pass", "message": msg, "mapping": mapping, "dep": dep})
            else:
                error_msg = result[0].get("error", "Unknown DB Error") if result else "No result returned"
                msg = f"❌ FAILED: {table}.{col} threw an error -> {error_msg}"
                print(msg)
                validation_log.append({"status": "fail", "message": msg, "mapping": mapping, "dep": dep})
            
    # --- THE FEEDBACK LOOP ---
    failed_mappings = [v for v in validation_log if v["status"] == "fail"]
    
    if failed_mappings:
        feedback = "The following dependencies failed database validation (table was empty or column didn't exist). Please choose an alternative table/column from your earlier tool results and submit the corrected mapping using SubmitFinalMapping:\n"
        for fail in failed_mappings:
            ui_name = fail['mapping']['logical_ui_name']
            bad_table = fail['dep']['physical_table']
            bad_col = fail['dep']['physical_column']
            feedback += f"- UI Metric '{ui_name}': Dependency {bad_table}.{bad_col} failed validation.\n"
        
        return {
            "validation_results": validation_log, 
            "final_mapping_json": tc['args'] if 'tc' in locals() else {"mappings": mappings},
            "messages": [ToolMessage(content=feedback, tool_call_id=tool_call_id)]
        }
    else:
        return {
            "validation_results": validation_log, 
            "final_mapping_json": tc['args'] if 'tc' in locals() else {"mappings": mappings},
            "messages": [ToolMessage(content="✅ All validations passed!", tool_call_id=tool_call_id)]
        }

# --- 5. Routing Logic ---
def route_agent_action(state: AgentState):
    last_message = state["messages"][-1]
    
    if last_message.tool_calls:
        for tool_call in last_message.tool_calls:
            if tool_call['name'] == 'SubmitFinalMapping':
                print("\n🎯 [Agent submitted final mapping. Routing to Validator...]")
                return "validate"
        
        tool_names = [tc['name'] for tc in last_message.tool_calls]
        print(f"🛠️  [Agent decided to use exploration tools: {', '.join(tool_names)}]")
        return "tools"
    
    return "end"

def route_after_validation(state: AgentState):
    last_message = state["messages"][-1].content
    if "failed database validation" in last_message:
        print("\n🔄 [Validation failed! Sending feedback back to Agent for correction...]")
        return "agent"
    
    print("\n🎉 [All mappings validated successfully! Ready for SQL Architect]")
    return "end"

# --- 6. Build the LangGraph ---
workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", ToolNode(tools))
workflow.add_node("validate", validate_mapping_node)

workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", route_agent_action, {"tools": "tools", "validate": "validate", "end": END})
workflow.add_edge("tools", "agent")
workflow.add_conditional_edges("validate", route_after_validation, {"agent": "agent", "end": END})

app = workflow.compile()

# --- SYSTEM PROMPT ---
system_prompt = """
You are an autonomous Database Schema Detective. Your job is to map logical UI terms to physical tables.

STRATEGY FOR METRICS:
1. For every logical metric the API requests, call `trace_metric_to_physical`.
2. The tool will output the EXACT formula (if it's derived) and the physical dependencies.
3. CRITICAL: You MUST map EVERY dependency returned by the tool into the `dependencies` array. Include the `formula` string exactly as provided by the tool. DO NOT guess or change table names.

STRATEGY FOR DIMENSIONS:
4. For every dimension requested in the payload, call the `trace_dimension_hierarchy` tool to find its exact table, primary key, and bridge details.

WHEN YOU ARE DONE:
Call `SubmitFinalMapping`.
- Include ALL metric mappings (with their formulas and nested dependencies arrays) and ALL dimension mappings.
- If the tool discovered a bridge table for a dimension, ensure `requires_bridge` is true and note the table name.

SELF-HEALING & CORRECTION:
- If a metric returns 'FAILED_BUT_SUGGESTS', choose the most logically similar column from the `suggestions` array.
- If the system replies that a mapping "failed database validation", look at the alternative tables returned by your earlier tool calls, choose a DIFFERENT physical table for that specific dependency, and call `SubmitFinalMapping` again until it passes.
"""

# --- EXECUTION ---
if __name__ == "__main__":
    payload_file = "payload.json"
    
    if not os.path.exists(payload_file):
        print(f"❌ Error: {payload_file} not found!")
        exit(1)
        
    with open(payload_file, "r") as f:
        payload = json.load(f)
        
    query_block = payload.get("variables", {}).get("query", {})
    
    # FIX: Safely handle null arrays using 'or []'
    measures = query_block.get("aggregatedMeasures") or []
    dimensions = query_block.get("dimensionLevels") or []
    
    # Also extract dimensions from the scope (filters) safely
    scope = query_block.get("scope") or {}
    filters = scope.get("dimensionFilters") or []
    filter_dims = [f.get("dimensionColumnName") for f in filters if f.get("dimensionColumnName")]
    
    all_dimensions = list(set(dimensions + filter_dims))
    target_tables = query_block.get("datatable") or []
    
    measures_str = "', '".join(measures) if measures else "None"
    dimensions_str = "', '".join(all_dimensions) if all_dimensions else "None"
    tables_str = "', '".join(target_tables)
    
    test_question = f"""
    I need to construct a SQL query based on an API payload.
    The API payload is asking for data from these target tables: '{tables_str}'.
    
    It is requesting these specific logical measures: '{measures_str}'.
    It is requesting these dimensions (Group Bys and Filters): '{dimensions_str}'.
    
    Can you figure out exactly which tables, columns, and bridge tables hold the data for these?
    """
    
    print(f"User: {test_question}\n" + "-"*50)
    
    initial_state = {
        "messages": [SystemMessage(content=system_prompt), HumanMessage(content=test_question)],
        "final_mapping_json": {},
        "validation_results": []
    }
    
    final_state = None
    for event in app.stream(initial_state, {"recursion_limit": 25}):
        if "validate" in event:
            final_state = event["validate"]
            
    # --- SAVE TO JSON FOR THE SQL ARCHITECT ---
    if final_state and "final_mapping_json" in final_state:
        output_file = "validated_schema_map.json"
        
        final_output = {
            "api_payload_context": {
                "measures": measures,
                "dimensions": dimensions,
                "filters": query_block.get("scope") or {}
            },
            "validated_mappings": final_state["final_mapping_json"]
        }
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(final_output, f, indent=4)
            
        print("\n" + "="*80)
        print(f"🎉 SUCCESS! Validated Schema Map saved to: {output_file}")
        print("="*80)
    else:
        print("\n❌ Execution failed or did not reach the validation phase.")
