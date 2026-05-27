import sys
import json
import subprocess
from typing import cast
from pathlib import Path
from typing_extensions import override

import pytest
from jinja2 import Environment, FileSystemLoader

from prisma import __version__
from prisma._compat import PYDANTIC_V2
from prisma.generator import (
    BASE_PACKAGE_DIR,
    Manifest,
    Generator,
    GenericGenerator,
    render_template,
    cleanup_templates,
)
from prisma.generator.utils import Faker, copy_tree

from .utils import assert_module_is_clean, assert_module_not_clean
from ..utils import Testdir


def test_repeated_rstrip_bug(tmp_path: Path) -> None:
    """Previously, rendering schema.prisma.jinja would have rendered the file
    to schema.prism instead of schema.prisma
    """
    env = Environment(loader=FileSystemLoader(str(tmp_path)))

    template = 'schema.prisma.jinja'
    tmp_path.joinpath(template).write_text('foo')
    render_template(tmp_path, template, dict(), env=env)

    assert tmp_path.joinpath('schema.prisma').read_text() == 'foo'


def test_template_cleanup(testdir: Testdir) -> None:
    """Cleaning up templates removes all rendered files"""
    path = testdir.path / 'prisma'
    assert not path.exists()
    copy_tree(BASE_PACKAGE_DIR, path)

    assert_module_not_clean(path)
    cleanup_templates(path)
    assert_module_is_clean(path)

    # ensure cleaning an already clean module doesn't change anything
    cleanup_templates(path)
    assert_module_is_clean(path)


def test_erroneous_template_cleanup(testdir: Testdir) -> None:
    """Template runtime errors do not result in a partially generated module"""
    path = testdir.path / 'prisma'
    copy_tree(BASE_PACKAGE_DIR, path)

    assert_module_not_clean(path)

    template = '{{ undefined.foo }}'
    template_path = testdir.path / 'prisma' / 'generator' / 'templates' / 'template.py.jinja'
    template_path.write_text(template)

    with pytest.raises(subprocess.CalledProcessError) as exc:
        testdir.generate()

    output = str(exc.value.output, sys.getdefaultencoding())
    assert template in output

    assert_module_is_clean(path)


def test_generation_version_number(testdir: Testdir) -> None:
    """Ensure the version number is shown when the client is generated"""
    stdout = testdir.generate().stdout.decode('utf-8')
    assert f'Generated Prisma Client Python (v{__version__})' in stdout


def test_faker() -> None:
    """Ensure Faker is re-playable"""
    iter1 = iter(Faker())
    iter2 = iter(Faker())
    first = [next(iter1) for _ in range(10)]
    second = [next(iter2) for _ in range(10)]
    assert first == second


def test_invoke_outside_generation() -> None:
    """Attempting to invoke a generator outside of Prisma generation errors"""
    with pytest.raises(RuntimeError) as exc:
        Generator.invoke()

    assert exc.value.args[0] == 'Attempted to invoke a generator outside of Prisma generation'


def test_invalid_type_argument() -> None:
    """Non-BaseModel argument to GenericGenerator raises an error"""

    class MyGenerator(GenericGenerator[Path]):  # type: ignore
        @override
        def get_manifest(self) -> Manifest:  # pragma: no cover
            return super().get_manifest()  # type: ignore

        @override
        def generate(self, data: Path) -> None:  # pragma: no cover
            raise NotImplementedError()

    with pytest.raises(TypeError) as exc:
        MyGenerator().data_class  # noqa: B018

    assert 'pathlib.Path' in exc.value.args[0]
    assert 'pydantic.main.BaseModel' in exc.value.args[0]

    class MyGenerator2(GenericGenerator[Manifest]):
        @override
        def get_manifest(self) -> Manifest:  # pragma: no cover
            return super().get_manifest()  # type: ignore

        @override
        def generate(self, data: Manifest) -> None:  # pragma: no cover
            raise NotImplementedError()

    data_class = MyGenerator2().data_class
    assert data_class == Manifest


def test_generator_subclass_mismatch() -> None:
    """Attempting to subclass Generator instead of BaseGenerator raises an error"""
    with pytest.raises(TypeError) as exc:

        class MyGenerator(Generator):  # pyright: ignore[reportUnusedClass]
            ...

    message = exc.value.args[0]
    assert 'cannot be subclassed, maybe you meant' in message
    assert 'BaseGenerator' in message


def test_error_handling(testdir: Testdir) -> None:
    """Config validation errors are returned through JSONRPC without a stack trace"""
    with pytest.raises(subprocess.CalledProcessError) as exc:
        testdir.generate(options='partial_type_generator = "foo"')

    output = cast(bytes, exc.value.output).decode('utf-8').strip()
    if PYDANTIC_V2:
        line = output.splitlines()[-2]
        assert (
            line
            == "  Value error, Could not find a python file or module at foo [type=value_error, input_value='foo', input_type=str]"
        )
    else:
        assert output.endswith(
            '\nError: \n'
            '1 validation error for PythonData\n'
            'generator -> config -> partial_type_generator -> spec\n'
            '  Could not find a python file or module at foo (type=value_error)'
        )


def test_schema_path_same_path(testdir: Testdir) -> None:
    """Generating to the same directory does not cause any errors due to schema copying

    https://github.com/RobertCraigie/prisma-client-py/issues/513
    """
    proc = testdir.generate(output='.')
    assert proc.returncode == 0
    assert 'Generated Prisma Client Python' in proc.stdout.decode('utf-8')


def test_js_bridge_package_generated_for_postgresql(
    testdir: Testdir,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRISMA_PY_ENGINE=js-bridge emits a private PostgreSQL Node bridge package."""
    monkeypatch.setenv('PRISMA_PY_ENGINE', 'js-bridge')
    schema = """
    datasource db {{
      provider = "postgresql"
      url      = "postgresql://postgres:prisma@localhost:5432/prisma"
    }}

    generator db {{
      provider = "{python} -m prisma"
      output = "{output}"
      {options}
    }}

    model User {{
      id    String @id @default(cuid())
      email String @unique
    }}
    """

    testdir.generate(schema=schema, python=sys.executable)

    bridge = testdir.path / 'prisma' / 'js_bridge'
    package_json = json.loads(bridge.joinpath('package.json').read_text())
    bridge_config = json.loads(bridge.joinpath('bridge.config.json').read_text())

    assert bridge.joinpath('runtime.mjs').exists()
    assert bridge.joinpath('README.md').read_text().startswith('# Prisma Client Python JS bridge package')
    assert package_json['private'] is True
    assert package_json['type'] == 'module'
    assert package_json['scripts'] == {
        'generate': 'prisma generate --schema ./schema.prisma',
        'db:push': 'prisma db push --schema ./schema.prisma',
        'start': 'node --import tsx ./runtime.mjs',
        'check': 'node --check ./runtime.mjs',
    }
    assert package_json['dependencies'] == {
        '@prisma/client': '7.8.0',
        '@prisma/adapter-pg': '7.8.0',
        'pg': '8.21.0',
        'tsx': '4.22.3',
    }
    assert package_json['devDependencies'] == {
        'prisma': '7.8.0',
    }
    assert package_json['prismaClientPython'] == {
        'provider': 'postgresql',
        'supportLevel': 'First',
        'protocolVersion': '2026-05-26.phase0.v1',
        'prismaVersion': '7.8.0',
        'runtime': './runtime.mjs',
    }
    assert bridge_config['prismaVersion'] == '7.8.0'
    assert bridge_config['adapterPackage'] == '@prisma/adapter-pg'
    assert bridge_config['driverPackage'] == 'pg'
    assert bridge_config['driverVersion'] == '8.21.0'
    assert bridge_config['tsxVersion'] == '4.22.3'
    assert bridge_config['clientModule'] == './generated/prisma/client.ts'
    assert bridge_config['deferredProviders']['sqlite']['support_level'] == 'Deferred'

    schema = bridge.joinpath('schema.prisma').read_text()
    assert 'url      = "postgresql://postgres:prisma@localhost:5432/prisma"' not in schema
    assert 'generator db' not in schema
    assert 'generator js_bridge_client' in schema
    assert 'provider               = "prisma-client"' in schema
    assert 'engineType             = "client"' in schema
    assert 'output                 = "./generated/prisma"' in schema
    assert bridge.joinpath('prisma.config.ts').read_text() == (
        "import { defineConfig, env } from 'prisma/config';\n\n"
        'export default defineConfig({\n'
        "  schema: './schema.prisma',\n"
        '  datasource: {\n'
        "    url: env('DATABASE_URL'),\n"
        '  },\n'
        '});\n'
    )

    # Python public generated surface remains present; the bridge package is private sidecar output.
    assert (testdir.path / 'prisma' / 'client.py').exists()
    assert (testdir.path / 'prisma' / 'models.py').exists()


def test_js_bridge_deferred_provider_diagnostic(
    testdir: Testdir,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferred providers fail early with an actionable JS bridge diagnostic."""
    monkeypatch.setenv('PRISMA_PY_ENGINE', 'js-bridge')

    schema = """
    datasource db {{
      provider = "sqlite"
      url      = "file:dev.db"
    }}

    generator db {{
      provider = "{python} -m prisma"
      output = "{output}"
      {options}
    }}

    model User {{
      id    String @id @default(cuid())
      email String @unique
    }}
    """

    with pytest.raises(subprocess.CalledProcessError) as exc:
        testdir.generate(schema=schema, python=sys.executable)

    output = exc.value.output.decode('utf-8')
    assert 'PROVIDER_DEFERRED: PRISMA_PY_ENGINE=js-bridge currently supports only PostgreSQL.' in output
    assert "Provider 'sqlite' is Deferred." in output
    assert 'Required adapter package when enabled: @prisma/adapter-better-sqlite3.' in output
    assert 'Use PRISMA_PY_ENGINE=rust-legacy while this provider is deferred.' in output
    assert 'docs/prisma7-js-bridge/phase0/adapter-support-matrix.md' in output
