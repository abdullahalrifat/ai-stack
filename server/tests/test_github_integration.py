from unittest.mock import patch

import pytest

from app.integrations.github import GitHubIntegrationError, comment_issue, inspect_issue


def test_github_reads_require_token():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(GitHubIntegrationError, match="GITHUB_TOKEN"):
            inspect_issue("owner", "repo", 1)


def test_github_writes_require_explicit_approval():
    with patch.dict("os.environ", {"GITHUB_TOKEN": "test"}):
        with pytest.raises(GitHubIntegrationError, match="explicit approval"):
            comment_issue("owner", "repo", 1, "hello")
