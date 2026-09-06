# agent.py
import asyncio
import os
import sys
from getpass import getpass
from pathlib import Path
from typing import Annotated
from typing_extensions import TypedDict
from dotenv import load_dotenv

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

# 1. Define State
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

# 2. Tool Wrapper
def create_mcp_tools(mcp_session: ClientSession):
    @tool
    async def get_balance(customer_id: str) -> str:
        """Get the savings account balance for a specific customer."""
        result = await mcp_session.call_tool("get_balance", arguments={"customer_id": customer_id})
        return result.content[0].text

    # --- ADD THIS WRAPPER ---
    @tool
    async def get_transactions(customer_id: str) -> str:
        """Get the last 5 recent transactions (credits and debits) for a specific customer."""
        result = await mcp_session.call_tool("get_transactions", arguments={"customer_id": customer_id})
        return result.content[0].text
    
    # Return both tools in the list
    return [get_balance, get_transactions]

# 3. Main Execution
async def run_agent():
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key and sys.stdin.isatty():
        try:
            api_key = getpass("Enter your OpenAI API key (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("\nAPI key entry cancelled.") from None
    if not api_key:
        raise SystemExit("Missing OPENAI_API_KEY. Set it in .env or your environment, or run in a terminal to enter it securely.")

    llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=api_key)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().with_name("server.py"))]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            tools = create_mcp_tools(session)
            llm_with_tools = llm.bind_tools(tools)

            async def call_model(state: AgentState):
                response = await llm_with_tools.ainvoke(state["messages"])
                return {"messages": [response]}

            tool_node = ToolNode(tools)

            workflow = StateGraph(AgentState)
            workflow.add_node("agent", call_model)
            workflow.add_node("tools", tool_node)

            workflow.add_edge(START, "agent")
            workflow.add_conditional_edges("agent", tools_condition)
            workflow.add_edge("tools", "agent")

            app = workflow.compile()

            # Test Query
            user_input = "What is the balance for CUST-001?"
            print(f"\nUser: {user_input}\n")
            
            state = {"messages": [("user", user_input)]}
            
            async for chunk in app.astream(state, stream_mode="values"):
                last_message = chunk["messages"][-1]
                if last_message.type == "ai":
                    if last_message.tool_calls:
                        print(f"Agent: Calling tool -> {last_message.tool_calls[0]['name']}")
                    elif last_message.content:
                        print(f"Agent: {last_message.content}")

if __name__ == "__main__":
    asyncio.run(run_agent())
