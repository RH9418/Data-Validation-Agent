import os
from typing import TypedDict, Annotated, Sequence
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# Import the tools we built in Step 1
from agent_tools import (
    search_tables_by_keyword,
    get_table_schema,
    search_columns_by_keyword,
    sample_column_data
)

# Set up Azure OpenAI Client
from langchain_openai import AzureChatOpenAI

# Load environment variables from .env file
load_dotenv()

# Initialize the Azure LLM using your specific credentials
llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_ENDPOINT"),
    api_key=os.getenv("AZURE_API_KEY") or os.getenv("ZURE_API_KEY"), # Handling the typo just in case!
    api_version=os.getenv("AZURE_API_VERSION", "2024-06-01"),
    azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o"),
    temperature=0
)

# 1. Define the tools
tools = [
    search_tables_by_keyword,
    get_table_schema,
    search_columns_by_keyword,
    sample_column_data
]

# Bind tools to the LLM
llm_with_tools = llm.bind_tools(tools)

# 2. Define the Graph State
class AgentState(TypedDict):
    # The `add_messages` function appends new messages to the list, rather than overwriting
    messages: Annotated[Sequence[BaseMessage], add_messages]

# 3. Define the Agent Node
def agent_node(state: AgentState):
    print("\n🧠 [Agent is thinking...]")
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}

# 4. Define the Routing Logic
def should_continue(state: AgentState):
    messages = state["messages"]
    last_message = messages[-1]
    
    # If the LLM decided to call a tool, route to the "tools" node
    if last_message.tool_calls:
        tool_names = [tc['name'] for tc in last_message.tool_calls]
        print(f"🛠️  [Agent decided to use tools: {', '.join(tool_names)}]")
        return "continue"
    
    # Otherwise, it has finished its task
    print("✅ [Agent has reached a conclusion.]")
    return "end"

# 5. Build the LangGraph
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("agent", agent_node)
workflow.add_node("tools", ToolNode(tools)) # ToolNode automatically executes the requested tools

# Add edges
workflow.set_entry_point("agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "continue": "tools",
        "end": END
    }
)
workflow.add_edge("tools", "agent") # After tools run, go back to the agent

# Compile the graph
app = workflow.compile()

# --- SYSTEM PROMPT ---
system_prompt = """
You are an autonomous Database Schema Detective. Your job is to map logical UI terms 
to their exact physical tables and columns in the database.

You have no prior knowledge of the database schema. You MUST use your tools to explore it.
Do NOT guess column or table names. Always verify they exist.

STRATEGY:
1. If a user asks for a logical measure (e.g., 'sum_sys_base_dollars_fcst'), search the columns for keywords like 'sys_base' or 'fcst'.
2. If you find multiple mapping tables (like 'override_type_measure_mapping'), you should inspect their schema and sample their data to see if they hold the answers.
3. Once you are 100% certain of the physical location of the data, provide a final summary mapping the requested UI terms to their physical database locations.
"""

if __name__ == "__main__":
    # Test Scenario: Ask the agent to find where a specific field from your API payload lives.
    test_question = "I need to construct a SQL query, but the API payload asks for 'sys_base_dollars_fcst' and 'actual_sales_and_roy_fcst_dollars'. Can you figure out exactly which tables and columns hold this data? Please show your reasoning."
    
    print(f"User: {test_question}\n" + "-"*50)
    
    initial_state = {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=test_question)
        ]
    }
    
    # Stream the events as the agent works through the graph
    for event in app.stream(initial_state, {"recursion_limit": 15}):
        for key, value in event.items():
            if key == "agent":
                # Print the final output if there are no tool calls
                if not value["messages"][-1].tool_calls:
                    print("\n📝 FINAL ANSWER:\n")
                    print(value["messages"][-1].content)
            elif key == "tools":
                print(f"📊 [Tool execution complete. Returning database results to the agent...]")
