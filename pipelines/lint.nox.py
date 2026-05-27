from pathlib import Path

import nox

from pipelines.utils import setup_env
from pipelines.utils.prisma import generate

PYREFLY_ONLY = 'pyrefly-only'


def get_session_python(session: nox.Session) -> str:
    """Return the Python executable inside the nox-managed environment."""
    executable = session.run('python', '-c', 'import sys; print(sys.executable)', silent=True)
    assert executable is not None
    return executable.strip()


def run_pyrefly(session: nox.Session) -> None:
    session.run('pyrefly', 'check', '--python-interpreter-path', get_session_python(session))


@nox.session
def lint(session: nox.Session) -> None:
    """Run all linters, including formatting checks and type-checking"""
    if any(arg != PYREFLY_ONLY for arg in session.posargs):
        session.error(f'Unsupported lint session arguments: {session.posargs}')

    setup_env(session)
    session.install('-r', 'pipelines/requirements/lint.txt')

    # TODO: pyright doesn't resolve types correctly if we don't install inplace
    session.install('-e', '.')

    generate(session)

    session.run('ruff', 'check')
    session.run('ruff', 'format', '--check')
    run_pyrefly(session)
    if PYREFLY_ONLY in session.posargs:
        return

    session.run('pyright')
    session.run('pyright', '--ignoreexternal', '--verifytypes', 'prisma')
    session.run('slotscheck', '-m', 'prisma', '--exclude-modules', '_vendor')


@nox.session
def mypy(session: nox.Session) -> None:
    setup_env(session)
    session.install('-r', 'pipelines/requirements/mypy.txt')
    session.install('.')

    generate(session)

    session.run(
        'coverage',
        'run',
        '-m',
        'mypy',
        '--show-traceback',
        '--namespace-packages',
        '--package',
        'prisma',
        '--package',
        'tests',
    )


@nox.session
def format(session: nox.Session) -> None:
    """Format Python source code and all Prisma Schema files"""
    session.install('-r', 'pipelines/requirements/deps/ruff.txt')
    session.install('-e', '.')

    session.run('ruff', 'format')
    session.run('ruff', 'check', '--fix')

    for entry in Path.cwd().glob('**/*.schema.prisma'):
        session.run('prisma', 'format', f'--schema={entry}')
