"""MCP (Model Context Protocol) Server para Origin.

Expone skills seleccionadas como herramientas MCP para que
otros agentes (Claude Desktop, Cline, etc.) las consuman.

Protocolo: JSON-RPC sobre HTTP POST + SSE para streaming.
"""

from typing import Dict, Any, List
import logging

logger = logging.getLogger("origin.skills.mcp")

# Skills expuestas vía MCP (solo las seguras para consumo externo)
MCP_ALLOWED_SKILLS = {
    "web_search": {"description": "Search the web via DuckDuckGo", "params": {"query": "string"}},
    "calculator": {"description": "Evaluate math expressions safely", "params": {"expression": "string"}},
    "datetime": {"description": "Get date/time info", "params": {"action": "string", "timezone": "string (optional)"}},
    "external_apis": {
        "description": "Weather, crypto, exchange rates, translation",
        "params": {"action": "string", **{}},
    },
    "system_info": {"description": "Get OS/CPU/RAM/disk info", "params": {"action": "string"}},
    "file_manager": {
        "description": "Read files (read-only operations only)",
        "params": {"action": "read", "path": "string"},
    },
}


class MCPServer:
    """Servidor MCP integrado en Origin.

    Se monta como router de FastAPI en api/main.py.
    Expone:
      POST /mcp/tools/list       → lista herramientas disponibles
      POST /mcp/tools/call        → ejecuta una herramienta
      GET  /mcp/sse               → SSE para streaming
    """

    def __init__(self, skill_executor):
        self._executor = skill_executor

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "description": meta["description"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        k: {"type": "string" if "string" in v else "object"} for k, v in meta.get("params", {}).items()
                    },
                },
            }
            for name, meta in MCP_ALLOWED_SKILLS.items()
        ]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name not in MCP_ALLOWED_SKILLS:
            return {"error": f"Unknown tool: {name}", "allowed": list(MCP_ALLOWED_SKILLS.keys())}

        if name == "file_manager":
            action = arguments.get("action", "")
            if action not in ("read", "list", "info", "search", "grep"):
                return {"error": f"Action '{action}' not allowed via MCP (read-only)"}

        result = await self._executor.execute(name, arguments)
        if result.get("success"):
            return {"result": result.get("result")}
        return {"error": result.get("error", "Execution failed")}

    def get_routes(self, mcp_path: str = "/mcp") -> Dict[str, Any]:
        """Retorna dict con rutas para montar en FastAPI."""
        return {
            "list_tools_path": f"{mcp_path}/tools/list",
            "call_tool_path": f"{mcp_path}/tools/call",
        }
