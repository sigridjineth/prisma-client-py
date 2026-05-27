from __future__ import annotations

import ast
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'pyproject.toml').exists())


def read(path: str) -> str:
    return ROOT.joinpath(path).read_text(encoding='utf-8')


def session_run_calls(path: str, function_name: str) -> list[list[str]]:
    tree = ast.parse(read(path))
    calls: list[list[str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue

        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == 'run'
                and isinstance(func.value, ast.Name)
                and func.value.id == 'session'
            ):
                continue

            args: list[str] = []
            for arg in child.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    args.append(arg.value)
            calls.append(args)

    return calls


def test_lint_session_runs_ruff_and_pyrefly_gates() -> None:
    lint_calls = session_run_calls('pipelines/lint.nox.py', 'lint')
    pyrefly_calls = session_run_calls('pipelines/lint.nox.py', 'run_pyrefly')

    assert ['ruff', 'check'] in lint_calls
    assert ['ruff', 'format', '--check'] in lint_calls
    assert 'run_pyrefly(session)' in read('pipelines/lint.nox.py')
    assert any(call[:3] == ['pyrefly', 'check', '--python-interpreter-path'] for call in pyrefly_calls)


def test_lint_requirements_install_pyrefly_pin() -> None:
    assert '-r deps/pyrefly.txt' in read('pipelines/requirements/lint.txt')
    assert ROOT.joinpath('pipelines/requirements/deps/pyrefly.txt').read_text(encoding='utf-8').startswith('pyrefly==')


def test_pyrefly_has_narrow_project_config() -> None:
    pyproject = read('pyproject.toml')

    assert '[tool.pyrefly]' in pyproject
    assert 'project-includes' in pyproject
    assert 'search-path' in pyproject
    assert '"src/prisma/engine/_js_bridge.py"' in pyproject
    assert '"tests/prisma7_js_bridge_contracts/test_contracts.py"' in pyproject
    assert '"tests/prisma7_js_bridge_contracts/test_live_postgres.py"' in pyproject


def test_ci_lint_job_uses_repo_lint_session() -> None:
    assert 'nox -s lint' in read('.github/workflows/test.yml')
