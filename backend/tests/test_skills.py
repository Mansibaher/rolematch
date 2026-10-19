"""Alias handling must merge true synonyms and keep meaningful differences apart."""

import pytest

from app.services.skills import get_taxonomy


def ids(text: str) -> list[str]:
    return get_taxonomy().find_ids(text)


@pytest.mark.parametrize(
    ("text", "expected", "forbidden"),
    [
        ("5+ years of Java experience", {"java"}, {"javascript"}),
        ("Strong JavaScript and TypeScript skills", {"javascript", "typescript"}, {"java"}),
        ("We use JS/TS on the frontend", {"javascript", "typescript"}, {"java"}),
        ("Proficiency in C++ or C#", {"cpp", "csharp"}, {"c"}),
        ("Embedded C programming", {"c"}, {"cpp", "csharp"}),
        ("We are a Series C startup", set(), {"c"}),
        ("Objective-C experience", set(), {"c"}),
        ("Built mobile apps in React Native", {"react_native"}, {"react"}),
        ("Must react to production incidents quickly", set(), {"react"}),
        ("Services written in Go and Golang", {"go"}, set()),
        ("Go to market strategy", set(), {"go"}),
        ("Experience with PostgreSQL", {"postgresql"}, {"sql"}),
        ("SQL and NoSQL databases", {"sql", "nosql"}, set()),
        ("Kubernetes (k8s) and Docker", {"kubernetes", "docker"}, set()),
        ("the rest of the team", set(), {"rest_api"}),
        ("RESTful API design", {"rest_api"}, set()),
        ("node.js backend", {"nodejs"}, set()),
        ("Python lambda functions", {"python"}, {"aws"}),
    ],
)
def test_alias_edge_cases(text, expected, forbidden):
    found = set(ids(text))
    assert expected <= found, f"missing {expected - found} in {found}"
    assert not (forbidden & found), f"unexpected {forbidden & found}"


def test_implications_are_transitive_but_not_identity():
    tax = get_taxonomy()
    implied = tax.implied_by("pytorch")
    assert {"deep_learning", "machine_learning", "python"} <= implied
    assert "pytorch" not in implied
    # Java never implies JavaScript and vice versa.
    assert "javascript" not in tax.implied_by("java")
    assert "java" not in tax.implied_by("javascript")


def test_resolve_user_typed_names():
    tax = get_taxonomy()
    assert tax.resolve("postgres") == "postgresql"
    assert tax.resolve("JS") == "javascript"
    assert tax.resolve("Java") == "java"
    assert tax.resolve("totally unknown skill") is None
