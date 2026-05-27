from __future__ import annotations

import os
import sys
import json
import shutil
import textwrap
import subprocess
from pathlib import Path

import pytest


def run_checked(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.returncode == 0, proc.stdout
    return proc


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for Prisma 7 JS bridge live tests')
@pytest.mark.skipif(shutil.which('npm') is None, reason='npm is required for Prisma 7 JS bridge live tests')
def test_generated_js_bridge_runs_live_postgresql_crud(tmp_path: Path) -> None:
    url = os.environ.get('POSTGRESQL_URL') or os.environ.get('DATABASE_URL')
    if not url:
        pytest.skip('POSTGRESQL_URL or DATABASE_URL is required for the live PostgreSQL bridge test')

    schema = tmp_path / 'schema.prisma'
    schema.write_text(
        textwrap.dedent(
            f"""
            datasource db {{
              provider = "postgresql"
              url      = env("POSTGRESQL_URL")
            }}

            generator db {{
              provider = "{sys.executable} -m prisma"
              output   = "./prisma"
            }}

            model User {{
              id    Int     @id @default(autoincrement())
              email String  @unique
              name  String?
            }}
            """
        ).strip()
    )

    env = {
        **os.environ,
        'PRISMA_PY_ENGINE': 'js-bridge',
        'POSTGRESQL_URL': url,
        'DATABASE_URL': url,
        'npm_config_cache': str(tmp_path / '.npm-cache'),
    }

    run_checked([sys.executable, '-m', 'prisma', 'generate', f'--schema={schema}'], cwd=tmp_path, env=env)

    bridge = tmp_path / 'prisma' / 'js_bridge'
    assert bridge.joinpath('runtime.mjs').exists()
    assert bridge.joinpath('schema.prisma').exists()
    assert 'url      = env("POSTGRESQL_URL")' not in bridge.joinpath('schema.prisma').read_text()

    run_checked(['npm', 'install', '--silent'], cwd=bridge, env=env)
    run_checked(['npm', 'run', 'generate', '--silent'], cwd=bridge, env=env)
    run_checked(['npm', 'run', 'db:push', '--silent'], cwd=bridge, env=env)
    run_checked(['npm', 'run', 'check', '--silent'], cwd=bridge, env=env)

    smoke = tmp_path / 'smoke.py'
    smoke.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            sys.path.insert(0, '.')

            from prisma import Prisma
            from prisma.engine.errors import JSBridgeError

            db = Prisma()
            db.connect()
            try:
                db.user.delete_many()
                created = db.user.create(data={'email': 'alice@example.com', 'name': 'Alice'})
                found = db.user.find_unique(where={'email': 'alice@example.com'})
                count = db.user.count()

                with db.batch_() as batch:
                    batch.user.create(data={'email': 'batch-1@example.com', 'name': 'Batch 1'})
                    batch.user.create(data={'email': 'batch-2@example.com', 'name': 'Batch 2'})

                batch_count = db.user.count()

                with db.tx() as tx:
                    tx.user.create(data={'email': 'commit@example.com', 'name': 'Committed'})

                committed = db.user.find_unique(where={'email': 'commit@example.com'})

                try:
                    with db.tx() as tx:
                        tx.user.create(data={'email': 'rollback@example.com', 'name': 'Rolled back'})
                        raise RuntimeError('force rollback')
                except RuntimeError:
                    pass

                rolled_back = db.user.find_unique(where={'email': 'rollback@example.com'})

                tx_closed_error = None
                with db.tx() as closed_tx:
                    closed_tx.user.create(data={'email': 'closed@example.com', 'name': 'Closed'})
                try:
                    closed_tx.user.count()
                except JSBridgeError as exc:
                    tx_closed_error = exc.code

                disconnect_manager = db.tx()
                disconnect_tx = disconnect_manager.start()
                disconnect_tx.user.create(data={'email': 'disconnect@example.com', 'name': 'Disconnect'})
                db.disconnect()
                db.connect()
                disconnect_rolled_back = db.user.find_unique(where={'email': 'disconnect@example.com'}) is None

                print(
                    json.dumps(
                        {
                            'created': created.email,
                            'found': found.email,
                            'count': count,
                            'batch_count': batch_count,
                            'tx_committed': committed.email if committed else None,
                            'tx_rolled_back': rolled_back is None,
                            'tx_closed_error': tx_closed_error,
                            'disconnect_rolled_back': disconnect_rolled_back,
                        },
                        sort_keys=True,
                    )
                )
            finally:
                if db.is_connected():
                    db.disconnect()
            """
        ).strip()
    )

    proc = run_checked([sys.executable, str(smoke)], cwd=tmp_path, env=env)
    payload = json.loads(proc.stdout.splitlines()[-1])
    assert payload == {
        'count': 1,
        'batch_count': 3,
        'created': 'alice@example.com',
        'found': 'alice@example.com',
        'tx_closed_error': 'TRANSACTION_CLOSED',
        'tx_committed': 'commit@example.com',
        'tx_rolled_back': True,
        'disconnect_rolled_back': True,
    }
