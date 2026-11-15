"""Portable deployment transaction tests; no SSH, systemd or production data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "trace-deploy.py"
SPEC = importlib.util.spec_from_file_location("trace_deploy_under_test", SCRIPT)
deploy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deploy
SPEC.loader.exec_module(deploy)
OLD, NEW = "a" * 40, "b" * 40


class FakeRuntime:
    """Model checkout, process state and failures independently of subprocesses."""
    def __init__(self):
        self.current, self.target = OLD, NEW
        self.branch, self.dirty, self.ancestor = "main", False, True
        self.origin = "git@github.com:Sefffzxc/TRACE.git"
        self.paths = ["backend/app/api/routers/auth.py"]
        self.events, self.mutations, self.failures = [], [], {}
        self.active = {"ai", "api"}

    def fail(self, event):
        self.events.append(event)
        if self.failures.get(event, 0):
            self.failures[event] -= 1
            raise deploy.DeployError("simulated failure")

    def git(self, *args):
        operation = args[0]
        self.fail(("git", operation))
        if operation == "symbolic-ref":
            return self.branch.encode()
        if operation == "status":
            return b" M backend/app/main.py" if self.dirty else b""
        if operation == "remote":
            return self.origin.encode()
        if operation == "rev-parse":
            return (self.current if args[1] == "HEAD" else self.target).encode()
        if operation == "merge-base":
            if not self.ancestor:
                raise deploy.DeployError("diverged history")
        elif operation == "diff":
            return b"\0".join(path.encode() for path in self.paths) + b"\0"
        elif operation in {"merge", "reset"}:
            self.mutations.append((operation, args[-1]))
            self.current = args[-1]
        elif operation != "fetch":
            raise AssertionError(f"unexpected git operation: {args}")
        return b""

    def service(self, action, component):
        self.mutations.append((action, component))
        self.fail((action, component, self.current))
        if action == "start":
            self.active.add(component)
        else:
            self.active.discard(component)

    def wait_healthy(self, component):
        self.fail(("health", component, self.current))
        if component not in self.active:
            raise deploy.DeployError("service stopped")

    def compile(self, paths):
        self.fail(("compile", self.current))

    def compile_current(self, components):
        self.fail(("compile-current", self.current))


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime = FakeRuntime()
        self.deployer = deploy.Deployer(self.runtime, Path(self.temporary.name))
        self.addCleanup(patch.stopall)
        patch.object(self.deployer, "log").start()

    def state(self):
        return json.loads(self.deployer.state_file.read_text(encoding="utf-8"))

    def test_unchanged_commit_does_not_stop_or_update_application(self):
        self.runtime.target = OLD
        self.assertEqual(self.deployer.run(), 0)
        self.assertEqual(self.runtime.mutations, [])
        self.assertEqual(self.runtime.active, {"ai", "api"})

    def test_docs_and_frontend_fast_forward_without_restarting(self):
        self.runtime.paths = ["README.md", "frontend/src/App.jsx"]
        self.assertEqual(self.deployer.run(), 0)
        self.assertEqual(self.runtime.mutations, [("merge", NEW)])
        self.assertEqual(self.state()["status"], "success")

    def test_api_change_restarts_only_api(self):
        self.assertEqual(self.deployer.run(), 0)
        self.assertEqual(self.runtime.mutations,
                         [("stop", "api"), ("merge", NEW), ("start", "api")])

    def test_ai_code_and_model_update_stop_api_first_start_ai_first(self):
        self.runtime.paths = ["ai-service/main.py", "ai-service/model/model.pkl"]
        self.assertEqual(self.deployer.run(), 0)
        self.assertEqual(self.runtime.mutations, [
            ("stop", "api"), ("stop", "ai"), ("merge", NEW),
            ("start", "ai"), ("start", "api"),
        ])

    def test_manual_changes_in_cumulative_diff_never_mutate_running_release(self):
        for path in ["backend/requirements.txt", "ai-service/requirements.txt",
                     "backend/app/models/student.py", "backend/app/db/session.py",
                     "backend/app/main.py", "backend/app/core/config.py",
                     "backend/alembic/versions/add_column.py", "deploy/Caddyfile"]:
            with self.subTest(path=path):
                self.runtime.paths = ["frontend/src/App.jsx", path]
                self.assertEqual(self.deployer.run(), 2)
                self.assertEqual(self.runtime.current, OLD)
                self.assertEqual(self.runtime.mutations, [])
                self.assertEqual(self.state()["status"], "manual-update-required")

    def test_checkout_fetch_and_baseline_failures_leave_application_untouched(self):
        cases = [("branch", "feature"), ("dirty", True), ("ancestor", False),
                 ("origin", "https://example.invalid/other.git")]
        for attribute, value in cases:
            with self.subTest(attribute=attribute):
                original = getattr(self.runtime, attribute)
                setattr(self.runtime, attribute, value)
                with self.assertRaises(deploy.DeployError):
                    self.deployer.run()
                self.assertEqual(self.runtime.mutations, [])
                setattr(self.runtime, attribute, original)
        for event in [("git", "fetch"), ("health", "ai", OLD)]:
            with self.subTest(event=event):
                self.runtime.failures[event] = 1
                with self.assertRaises(deploy.DeployError):
                    self.deployer.run()
                self.assertEqual(self.runtime.mutations, [])

    def test_syntax_or_health_failure_restores_and_quarantines_failed_commit(self):
        for event in [("compile", NEW), ("health", "api", NEW),
                      ("start", "api", NEW), ("git", "merge")]:
            with self.subTest(event=event):
                self.runtime.failures[event] = 1
                self.assertEqual(self.deployer.run(retry=True), 1)
                self.assertEqual(self.runtime.current, OLD)
                self.assertEqual(self.runtime.active, {"ai", "api"})
                self.assertEqual(self.state()["status"], "failed")
                mutations = list(self.runtime.mutations)
                self.assertEqual(self.deployer.run(), 0)
                self.assertEqual(self.runtime.mutations, mutations)
        self.assertEqual(self.deployer.run(retry=True), 0)
        self.assertEqual(self.runtime.current, NEW)

    def test_a_new_fix_commit_can_deploy_without_retry_override(self):
        self.runtime.failures[("compile", NEW)] = 1
        self.assertEqual(self.deployer.run(), 1)
        self.runtime.target = "c" * 40
        self.assertEqual(self.deployer.run(), 0)
        self.assertEqual(self.runtime.current, "c" * 40)

    def test_rollback_failure_blocks_future_deployments_even_with_retry(self):
        self.runtime.failures.update({("compile", NEW): 1, ("git", "reset"): 1})
        self.assertEqual(self.deployer.run(), 1)
        self.assertEqual(self.state()["status"], "recovery-required")
        self.assertFalse(self.state()["restored"])
        mutations = list(self.runtime.mutations)
        with self.assertRaises(deploy.DeployError):
            self.deployer.run(retry=True)
        self.assertEqual(self.runtime.mutations, mutations)

    def test_interrupted_transaction_recovers_before_any_new_fetch(self):
        self.runtime.current = NEW
        self.runtime.active.clear()
        self.deployer.save_state("in-progress", OLD, NEW, ("ai", "api"))
        self.assertEqual(self.deployer.run(), 1)
        self.assertEqual(self.runtime.current, OLD)
        self.assertEqual(self.runtime.active, {"ai", "api"})
        self.assertEqual(self.state()["status"], "failed")
        self.assertNotIn(("git", "fetch"), self.runtime.events)

    def test_interrupted_recovery_does_not_overwrite_external_edits(self):
        self.runtime.current = "c" * 40
        self.deployer.save_state("in-progress", OLD, NEW, ("api",))
        self.assertEqual(self.deployer.run(), 1)
        self.assertEqual(self.runtime.current, "c" * 40)
        self.assertEqual(self.runtime.mutations, [])
        self.assertEqual(self.state()["status"], "recovery-required")


class BoundaryTests(unittest.TestCase):
    def test_payload_requires_loaded_model_and_valid_schema(self):
        for payload in [None, [], {"status": "error"}, {"status": "ok", "model_loaded": 1}]:
            self.assertFalse(deploy.healthy_payload("ai", payload))
        for payload in [{"status": "ok", "schema": None},
                        {"status": "ok", "schema": {"up_to_date": False}}]:
            self.assertFalse(deploy.healthy_payload("api", payload))
        self.assertTrue(deploy.healthy_payload("ai", {"status": "ok", "model_loaded": True}))
        self.assertTrue(deploy.healthy_payload("api", {"status": "ok"}))

    def test_git_and_syntax_checks_use_trace_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            source = repo / "backend" / "example.py"
            source.parent.mkdir()
            source.write_text("answer = 42\n", encoding="utf-8")
            runtime = deploy.Runtime(repo)
            with patch.object(runtime, "as_trace", return_value=b"") as trace, \
                    patch.object(runtime, "command", side_effect=AssertionError("ran as root")):
                runtime.git("status", "--porcelain")
                runtime.compile(["backend/example.py"])
            self.assertEqual(trace.call_count, 2)
            self.assertIn("/usr/bin/git", trace.call_args_list[0].args[0])
            self.assertIn(str(source), trace.call_args_list[1].args[0])

    def test_root_service_operations_are_allowlisted(self):
        runtime = deploy.Runtime()
        with patch.object(runtime, "command", return_value=b"") as command:
            for action, component in [("restart", "api"), ("start", "ssh"),
                                      ("stop", "api.service; arbitrary")]:
                with self.subTest(action=action, component=component):
                    with self.assertRaises(deploy.DeployError):
                        runtime.service(action, component)
            command.assert_not_called()
            runtime.service("start", "api")
            self.assertEqual(command.call_args.args[0],
                             ["/usr/bin/systemctl", "start", "trace-api.service"])


if __name__ == "__main__":
    unittest.main()
