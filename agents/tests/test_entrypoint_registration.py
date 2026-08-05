from app.tools.registry import registry


def test_app_entrypoint_registers_executor_tools():
    import app.main  # noqa: F401  (importing the deployment target must register tools)

    tools = registry.list_tools()
    assert "list_files" in tools
    assert "read_file" in tools
    assert "write_file" in tools
    assert "run_tests" in tools
