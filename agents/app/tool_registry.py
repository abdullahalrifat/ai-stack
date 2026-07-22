from typing import Dict, Callable, Any


class ToolRegistry:

    def __init__(self):

        self.tools: Dict[str, Callable] = {}



    def register(
        self,
        name: str,
        function: Callable
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


        return tool(
            **args
        )



registry = ToolRegistry()