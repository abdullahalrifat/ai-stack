from ..integrations.github import comment_issue, inspect_issue, inspect_pull_request, list_pull_request_files


def github_issue(owner: str, repo: str, number: int) -> dict:
    return inspect_issue(owner, repo, number)


def github_pull_request(owner: str, repo: str, number: int) -> dict:
    return inspect_pull_request(owner, repo, number)


def github_pull_request_files(owner: str, repo: str, number: int) -> dict:
    return list_pull_request_files(owner, repo, number)


def github_comment(owner: str, repo: str, number: int, body: str, approved: bool = False) -> dict:
    return comment_issue(owner, repo, number, body, approved=approved)
