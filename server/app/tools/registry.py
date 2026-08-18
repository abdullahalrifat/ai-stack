import logging
from typing import Any

from app.core.exceptions import RunCancelled

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Any] = {}

    def register(self, name: str, function: Any):
        self.tools[name] = function

    def get(self, name: str):
        if name not in self.tools:
            raise Exception(f"Tool not found: {name}")
        return self.tools[name]

    def list_tools(self):
        return list(self.tools.keys())

    def execute(self, name: str, args: dict):
        tool = self.get(name)

        logger.debug("Executing tool %s (%s) with args=%s", name, type(tool), args)

        if hasattr(tool, "invoke"):
            try:
                return tool.invoke(args)
            except RunCancelled:
                raise
            except Exception as e:
                logger.exception("Tool %s raised an exception", name)
                return {"tool_error": str(e), "tool": name, "args": args}

        try:
            return tool(**args)
        except RunCancelled:
            raise
        except Exception as e:
            logger.exception("Tool %s raised an exception", name)
            return {"tool_error": str(e), "tool": name, "args": args}


registry = ToolRegistry()
