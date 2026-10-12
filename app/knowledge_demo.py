import asyncio
import json

from app.agent.mcp_client import search_documents


async def main() -> None:
    queries = [
        "Onboard Acme",
        "What security requirements apply to Acme?",
        "Find the latest invoice issued to Acme",
        "quantum bicycle parking on Mars",
    ]
    for query in queries:
        print(f"\nQuery: {query}")
        print(json.dumps(json.loads(await search_documents(query)), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
