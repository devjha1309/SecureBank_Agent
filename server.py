# server.py
from mcp.server.mcpserver import MCPServer

# Initialize the MCP Server (v2 syntax)
mcp = MCPServer("AccountsServer")

@mcp.tool()
def get_balance(customer_id: str) -> str:
    """Get the savings account balance for a specific customer."""
    mock_database = {
        "CUST-001": "24,345 INR",
        "CUST-002": "18,200 INR"
    }
    return mock_database.get(customer_id, "Error: Customer ID not found.")

@mcp.tool()
def get_transactions(customer_id: str) -> str:
    """Get the last 5 recent transactions (credits and debits) for a specific customer."""
    mock_transactions = {
        "CUST-001": "1. Amazon: -4,500 INR\n2. Starbucks: -450 INR\n3. Salary: +150,000 INR",
        "CUST-002": "1. Uber: -850 INR\n2. Grocery: -12,000 INR"
    }
    return mock_transactions.get(customer_id, "Error: No transactions found.")

if __name__ == "__main__":
    mcp.run()