import pytest

from app.core.permissions import FULL_WRITE, PermissionPolicy, permissions_context


@pytest.fixture(autouse=True)
def _legacy_ci_contracts(request):
    """Keep older focused command tests aligned with the current permission API."""
    command_tests = {
        "test_run_tests_coverage_uses_explicit_package_target",
        "test_run_tests_supports_focused_pytest_node",
        "test_run_tests_supports_focused_ruff_file",
    }
    if request.node.name in command_tests:
        with permissions_context(PermissionPolicy(scope=FULL_WRITE)):
            yield
        return

    yield
