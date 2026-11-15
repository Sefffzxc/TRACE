#!/usr/bin/python3
"""Poll the existing main checkout and deploy code without changing dependencies.

Installed as a root-owned file outside the checkout. Git and Python compilation
always run as trace; root only manages the two fixed application services.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener


REPO = Path('/opt/trace')
STATE_DIR = Path('/var/lib/trace-deploy')
COMPONENTS = ('ai', 'api')
REMOTES = {
    'git@github.com:Sefffzxc/TRACE.git',
    'git@github.com:Sefffzxc/lbca.git',
}
MANUAL_FILES = {
    'backend/app/main.py', 'backend/app/core/config.py',
    'pyproject.toml', 'setup.py', 'setup.cfg', '.python-version',
    'poetry.lock', 'uv.lock', 'Pipfile', 'Pipfile.lock',
}


class DeployError(RuntimeError):
    pass


def requires_manual_update(path: str) -> bool:
    name = path.rsplit('/', 1)[-1]
    return (
        path in MANUAL_FILES
        or name in MANUAL_FILES
        or name.startswith('requirements')
        or name.endswith('.env') or name.startswith('.env.')
        or path.startswith(('deploy/', 'backend/app/models/', 'backend/app/db/'))
        or any(part in {'migrations', 'alembic'} for part in path.split('/'))
    )


def affected_components(paths: list[str]) -> tuple[str, ...]:
    # Stop the API too when changing AI, so requests cannot cross the update.
    if any(path.startswith('ai-service/') for path in paths):
        return COMPONENTS
    if any(path.startswith('backend/') for path in paths):
        return ('api',)
    return ()


def healthy_payload(component: str, data: object) -> bool:
    if not isinstance(data, dict) or data.get('status') != 'ok':
        return False
    if component == 'ai':
        return data.get('model_loaded') is True
    schema = data.get('schema', {})
    return isinstance(schema, dict) and schema.get('up_to_date', True) is True


class Runtime:
    def __init__(self, repo: Path = REPO):
        self.repo = repo

    def command(self, args: list[str], label: str, timeout: int = 45) -> bytes:
        # Capture output: never copy application logs, configuration or raw
        # exceptions into the deployment journal.
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise DeployError(f'{label} timed out') from None
        if process.returncode:
            raise DeployError(f'{label} failed')
        return output

    def as_trace(self, args: list[str], label: str, timeout: int = 45) -> bytes:
        return self.command([
            '/usr/sbin/runuser', '-u', 'trace', '--', '/usr/bin/env',
            'HOME=/home/trace', 'GIT_TERMINAL_PROMPT=0',
            'GIT_SSH_COMMAND=/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10',
            *args,
        ], label, timeout)

    def git(self, *args: str) -> bytes:
        return self.as_trace([
            '/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.fsmonitor=false', '-C', str(self.repo), *args,
        ], 'git ' + args[0])

    def service(self, action: str, component: str) -> None:
        if action not in {'start', 'stop'} or component not in COMPONENTS:
            raise DeployError('Invalid service operation')
        self.command([
            '/usr/bin/systemctl', action, f'trace-{component}.service',
        ], f'{component} {action}', timeout=45)

    def compile(self, paths: list[str]) -> None:
        files = [str(self.repo / path) for path in paths
                 if path.endswith('.py') and (self.repo / path).is_file()
                 and path.startswith(('backend/', 'ai-service/'))]
        if files:
            self.as_trace(['/usr/bin/python3', '-m', 'py_compile', *files],
                          'Python syntax check')

    def compile_current(self, components: tuple[str, ...]) -> None:
        paths = []
        roots = {'api': self.repo / 'backend/app', 'ai': self.repo / 'ai-service'}
        for component in components:
            for directory, names, files in os.walk(roots[component]):
                names[:] = [name for name in names
                            if not name.startswith('.') and name != '__pycache__']
                paths.extend(str((Path(directory) / name).relative_to(self.repo)).replace(os.sep, '/')
                             for name in files if name.endswith('.py'))
        # Recompile restored sources too: a fast rollback can otherwise reuse
        # timestamp-based bytecode from the failed commit when file sizes match.
        self.compile(paths)

    def wait_healthy(self, component: str, timeout: int = 60) -> None:
        port = {'api': 8000, 'ai': 8001}[component]
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + timeout
        while True:
            try:
                with opener.open(f'http://127.0.0.1:{port}/health', timeout=3) as response:
                    data = json.loads(response.read(65537))
                if healthy_payload(component, data):
                    return
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise DeployError(f'{component} did not become healthy')
            time.sleep(2)


class Deployer:
    def __init__(self, runtime: Runtime, state_dir: Path = STATE_DIR):
        self.runtime = runtime
        self.state_file = state_dir / 'status.json'

    def log(self, message: str) -> None:
        print(message, flush=True)

    def git_text(self, *args: str) -> str:
        return self.runtime.git(*args).decode('utf-8', errors='strict').strip()

    def head(self) -> str:
        value = self.git_text('rev-parse', 'HEAD')
        if not re.fullmatch(r'[0-9a-f]{40}', value):
            raise DeployError('Invalid checkout commit')
        return value

    def clean_main(self) -> None:
        if self.git_text('symbolic-ref', '--short', 'HEAD') != 'main':
            raise DeployError('Checkout must be on main')
        if self.git_text('status', '--porcelain', '--untracked-files=no'):
            raise DeployError('Tracked files have local changes; update manually')

    def read_state(self) -> dict:
        if not self.state_file.exists():
            return {}
        try:
            state = json.loads(self.state_file.read_text(encoding='utf-8'))
            if not isinstance(state, dict):
                raise ValueError()
            return state
        except (ValueError, OSError):
            raise DeployError('Cannot read deployment state; inspect it privately') from None

    def save_state(self, status: str, previous: str, target: str,
                   components: tuple[str, ...], **extra: object) -> None:
        data = dict(status=status, previous=previous, target=target,
                    components=list(components), updated_at=int(time.time()), **extra)
        temporary = self.state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, self.state_file)

    def check(self) -> None:
        self.clean_main()
        if self.git_text('remote', 'get-url', 'origin') not in REMOTES:
            raise DeployError('Origin must use the TRACE GitHub SSH URL')
        for component in COMPONENTS:
            self.runtime.wait_healthy(component)
        self.log('OK: clean main checkout; API and AI are healthy')

    def restore(self, previous: str, target: str,
                components: tuple[str, ...]) -> bool:
        try:
            # Do not overwrite an administrator's edits, a different branch,
            # or a commit installed outside this deployment transaction.
            self.clean_main()
            if self.head() not in {previous, target}:
                raise DeployError('Checkout changed during deployment')
            for component in reversed(components):
                self.runtime.service('stop', component)
            if self.head() == target and previous != target:
                self.runtime.git('reset', '--keep', previous)
            self.runtime.compile_current(components)
            for component in components:
                self.runtime.service('start', component)
                self.runtime.wait_healthy(component)
            self.log(f'RESTORED: {previous[:12]} is healthy')
            return True
        except DeployError:
            self.log('RECOVERY REQUIRED: inspect code and services privately; timer will not deploy')
            return False

    def run(self, retry: bool = False) -> int:
        state = self.read_state()
        if state.get('status') == 'in-progress':
            previous, target = state.get('previous', ''), state.get('target', '')
            components = tuple(state.get('components', []))
            if (not re.fullmatch(r'[0-9a-f]{40}', previous)
                    or not re.fullmatch(r'[0-9a-f]{40}', target)
                    or components not in ((), ('api',), COMPONENTS)):
                raise DeployError('Invalid interrupted deployment state')
            self.log('Interrupted deployment detected; restoring previous code')
            restored = self.restore(previous, target, components)
            self.save_state('failed' if restored else 'recovery-required',
                            previous, target, components, restored=restored)
            return 1
        if state.get('status') == 'recovery-required':
            raise DeployError('Manual recovery required before enabling deployment again')

        self.clean_main()
        if self.git_text('remote', 'get-url', 'origin') not in REMOTES:
            raise DeployError('Origin must use the TRACE GitHub SSH URL')
        previous = self.head()
        self.runtime.git('fetch', '--no-tags', '--no-recurse-submodules', 'origin',
                         '+refs/heads/main:refs/remotes/origin/main')
        target = self.git_text('rev-parse', 'refs/remotes/origin/main')
        if not re.fullmatch(r'[0-9a-f]{40}', target):
            raise DeployError('Invalid remote commit')
        if previous == target:
            self.log(f'Up to date: {target[:12]}')
            return 0
        if state.get('status') == 'failed' and state.get('target') == target and not retry:
            self.log(f'SKIPPED: {target[:12]} previously failed; push a fix or retry manually')
            return 0
        self.runtime.git('merge-base', '--is-ancestor', previous, target)
        raw_paths = self.runtime.git('diff', '--name-only', '--no-renames', '-z', previous, target)
        paths = [path.decode('utf-8', errors='strict') for path in raw_paths.split(b'\0') if path]
        components = affected_components(paths)
        if any(requires_manual_update(path) for path in paths):
            self.save_state('manual-update-required', previous, target, components)
            self.log(f'MANUAL UPDATE REQUIRED: {target[:12]} changes dependencies, schema, startup or server configuration')
            return 2
        if components:
            # Never disturb a running release if the baseline already fails.
            for component in COMPONENTS:
                self.runtime.wait_healthy(component)
        self.clean_main()
        if self.head() != previous:
            raise DeployError('Checkout changed while checking for an update')

        self.save_state('in-progress', previous, target, components)
        self.log(f'Deploying {previous[:12]} -> {target[:12]}')
        try:
            for component in reversed(components):
                self.runtime.service('stop', component)
            self.runtime.git('merge', '--ff-only', '--no-edit', target)
            if self.head() != target:
                raise DeployError('Checkout did not reach the expected commit')
            self.runtime.compile(paths)
            for component in components:
                self.runtime.service('start', component)
                self.runtime.wait_healthy(component)
            self.save_state('success', previous, target, components)
            self.log(f'OK: deployed {target[:12]}' + ('; services healthy' if components else '; no application restart needed'))
            return 0
        except DeployError as error:
            self.log(f'FAILED: {error}; restoring previous code')
            restored = self.restore(previous, target, components)
            self.save_state('failed' if restored else 'recovery-required',
                            previous, target, components, restored=restored)
            return 1


@contextlib.contextmanager
def deployment_lock():
    # Imported here so the decision logic can be tested on Windows too.
    import fcntl
    with (STATE_DIR / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeployError('Another deployment is running') from None
        yield


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Check checkout and service health without fetching or updating')
    mode.add_argument('--retry', action='store_true', help='Retry the current remote commit after a previous failed deployment')
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('Run the installed deployment script with sudo.', file=sys.stderr)
        return 1
    os.umask(0o077)
    try:
        with deployment_lock():
            deployer = Deployer(Runtime())
            if args.check:
                deployer.check()
                return 0
            return deployer.run(retry=args.retry)
    except DeployError as error:
        print(f'FAILED: {error}', flush=True)
        return 1
    except Exception:
        # Raw exceptions can contain repository/configuration values.
        print('FAILED: unexpected deployment error; inspect the checkout and private state', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
