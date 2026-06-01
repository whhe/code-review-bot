from code_review_bot.config import _github_graphql_url, _github_rest_api_base


def test_github_com_uses_api_subdomain() -> None:
    rest = _github_rest_api_base("https://github.com/octocat/hello-world.git")
    assert rest == "https://api.github.com"
    assert _github_graphql_url(rest) == "https://api.github.com/graphql"


def test_github_com_www_host() -> None:
    rest = _github_rest_api_base("https://www.github.com/octocat/hello-world.git")
    assert rest == "https://api.github.com"


def test_ghes_derives_host_api_v3() -> None:
    rest = _github_rest_api_base("https://github.corp.example.com/acme/widget.git")
    assert rest == "https://github.corp.example.com/api/v3"
    assert _github_graphql_url(rest) == "https://github.corp.example.com/api/graphql"


def test_ghes_with_port() -> None:
    rest = _github_rest_api_base("https://github.corp.example.com:8443/acme/widget.git")
    assert rest == "https://github.corp.example.com:8443/api/v3"
    assert _github_graphql_url(rest) == "https://github.corp.example.com:8443/api/graphql"


def test_platform_url_override_full_rest_base() -> None:
    rest = _github_rest_api_base(
        "https://github.corp.example.com/acme/widget.git",
        "https://github.corp.example.com/api/v3",
    )
    assert rest == "https://github.corp.example.com/api/v3"
    assert _github_graphql_url(rest) == "https://github.corp.example.com/api/graphql"


def test_platform_url_override_host_only_appends_api_v3() -> None:
    rest = _github_rest_api_base(
        "https://github.corp.example.com/acme/widget.git",
        "https://github.corp.example.com",
    )
    assert rest == "https://github.corp.example.com/api/v3"


def test_platform_url_override_github_com_normalizes_to_api() -> None:
    rest = _github_rest_api_base(
        "https://github.com/octocat/hello-world.git",
        "https://github.com",
    )
    assert rest == "https://api.github.com"
