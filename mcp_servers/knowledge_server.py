import json
import sys
from pathlib import Path

from fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.retrieval.lexical import read_document as read_knowledge_document
from app.retrieval.lexical import search_documents as retrieve_documents

mcp = FastMCP("Northstar Knowledge Server")


@mcp.tool
def search_documents(query: str) -> str:
    """Search the local Northstar Markdown knowledge base."""
    return json.dumps(retrieve_documents(query), ensure_ascii=False)


@mcp.tool
def read_document(source: str) -> str:
    """Return the full text of one knowledge-base Markdown document by path."""
    return json.dumps(read_knowledge_document(source), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
