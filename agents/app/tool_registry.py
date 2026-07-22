from typing import Dict, Callable, Any


class ToolRegistry:

    def __init__(self):
        self.tools: Dict[str, Any] = {}


    def register(
        self,
        name: str,
        function: Any
    ):

        self.tools[name] = function



    def get(
        self,
        name: str
    ):

        if name not in self.tools:
            raise Exception(
                f"Tool not found: {name}"
            )

        return self.tools[name]



    def list_tools(self):

        return list(
            self.tools.keys()
        )



    def execute(
    self,
    name: str,
    args: dict
    ):

        tool = self.get(name)


        print(
            "TOOL TYPE:",
            type(tool)
        )


        if hasattr(tool, "invoke"):

            try:

                return tool.invoke(
                    args
                )

            except Exception as e:

                return {
                    "tool_error": str(e),
                    "tool": name,
                    "args": args
                }


        try:

            return tool(**args)

        except Exception as e:

            return {
                "tool_error": str(e),
                "tool": name,
                "args": args
            }



registry = ToolRegistry()