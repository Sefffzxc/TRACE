"""The tenant boundary, checked rather than asserted.

Two claims are made about this system and both are testable:

  1. The system operator (super admin) cannot read any school's student data.
  2. One school's staff cannot read another school's records.

Neither is a UI rule. Both live in `app/api/deps.py`, and this file checks that
every endpoint which touches student data actually depends on that gate — so
the guarantee survives somebody adding an endpoint later and forgetting.

Running it
----------
    python tests/test_tenant_boundary.py

It needs no database and no server. `fastapi` and `sqlalchemy` are stubbed so
the dependency functions can be called directly; `app/api/deps.py` itself is
the real file, and the role constants are parsed out of the real
`app/models/staff.py` so the test cannot pass against a stale copy of them.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Routers whose endpoints read or write a school's own records. Every routed
# endpoint in these files must take a school scope.
TENANT_ROUTERS = {"students.py", "pace.py", "ai.py", "dashboard.py", "academic.py"}

# The only permitted exceptions, each with the reason it is one. An endpoint
# may be listed here when it returns nothing belonging to any school. Adding a
# name here is a deliberate act that shows up in review; forgetting to scope an
# endpoint is not.
UNSCOPED_BY_DESIGN = {
    "ai.py:ai_status": "service health — no student, school or record in the response",
    "ai.py:ai_model_card": "describes the model itself; metrics come from the "
                           "published evaluation, not from any school's data",
}


def _install_stubs(role_constants: dict) -> None:
    fa = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            self.status_code, self.detail = status_code, detail
            super().__init__(detail)

    fa.HTTPException = HTTPException
    fa.Depends = lambda *a, **k: None
    fa.Request = object
    fa.status = types.SimpleNamespace(
        HTTP_401_UNAUTHORIZED=401, HTTP_403_FORBIDDEN=403,
        HTTP_404_NOT_FOUND=404, HTTP_429_TOO_MANY_REQUESTS=429,
    )
    sec = types.ModuleType("fastapi.security")
    sec.HTTPBearer = lambda **k: None
    sec.HTTPAuthorizationCredentials = object

    sa = types.ModuleType("sqlalchemy")
    sa.select = lambda *a, **k: None
    sax = types.ModuleType("sqlalchemy.ext.asyncio")
    sax.AsyncSession = object

    dbs = types.ModuleType("app.db.session")
    dbs.get_db = lambda: None
    schools = types.ModuleType("app.models.schools")
    schools.School = type("School", (), {})
    schools.SCHOOL_APPROVED, schools.SCHOOL_PENDING = "approved", "pending"
    stf = types.ModuleType("app.models.staff")
    for key, value in role_constants.items():
        setattr(stf, key, value)
    stf.Staff = type("Staff", (), {})
    seco = types.ModuleType("app.core.security")
    seco.decode_token = lambda token: None

    sys.modules.update({
        "fastapi": fa, "fastapi.security": sec,
        "sqlalchemy": sa, "sqlalchemy.ext.asyncio": sax,
        "app.db.session": dbs, "app.models.schools": schools,
        "app.models.staff": stf, "app.core.security": seco,
    })
    return HTTPException


class FakeStaff:
    def __init__(self, role: str, school_id):
        self.role, self.school_id = role, school_id


def main() -> int:
    source = (ROOT / "app" / "models" / "staff.py").read_text()
    roles = dict(re.findall(r'^(ROLE_\w+)\s*=\s*"([^"]+)"', source, re.M))
    assert {"ROLE_SUPER_ADMIN", "ROLE_ADMIN", "ROLE_TEACHER"} <= roles.keys(), roles

    HTTPException = _install_stubs(roles)
    sys.path.insert(0, str(ROOT))
    from app.api.deps import get_super_admin, school_admin_scope, school_scope

    async def call(fn, staff):
        try:
            return ("ALLOWED", await fn(staff))
        except HTTPException as exc:
            return (f"REFUSED {exc.status_code}", exc.detail)

    async def run() -> None:
        operator = FakeStaff(roles["ROLE_SUPER_ADMIN"], None)
        principal = FakeStaff(roles["ROLE_ADMIN"], 1)
        teacher_a = FakeStaff(roles["ROLE_TEACHER"], 1)
        teacher_b = FakeStaff(roles["ROLE_TEACHER"], 2)

        print("=" * 78)
        print("1. school_scope — the gate every student-data endpoint depends on")
        print("=" * 78)
        for label, who in [("system operator", operator), ("principal, school 1", principal),
                           ("teacher, school 1", teacher_a), ("teacher, school 2", teacher_b)]:
            verdict, detail = await call(school_scope, who)
            print(f"   {label:22} {verdict:12} {str(detail)[:52]}")

        verdict, _ = await call(school_scope, operator)
        assert verdict.startswith("REFUSED 403"), "the operator reached student data"
        assert (await call(school_scope, teacher_a))[1] == 1
        assert (await call(school_scope, teacher_b))[1] == 2
        print("   -> operator refused; each teacher receives only their own school id")

        print()
        print("=" * 78)
        print("2. Actions reserved to a school's own administrator")
        print("=" * 78)
        for label, who in [("system operator", operator), ("principal", principal),
                           ("teacher", teacher_a)]:
            verdict, detail = await call(school_admin_scope, who)
            print(f"   {label:22} {verdict:12} {str(detail)[:52]}")
        assert (await call(school_admin_scope, operator))[0].startswith("REFUSED")
        assert (await call(school_admin_scope, teacher_a))[0].startswith("REFUSED")
        assert (await call(school_admin_scope, principal))[1] == 1

        print()
        print("=" * 78)
        print("3. Operator actions — a principal must not reach them either")
        print("=" * 78)
        for label, who in [("system operator", operator), ("principal", principal),
                           ("teacher", teacher_a)]:
            verdict, _ = await call(get_super_admin, who)
            print(f"   {label:22} {verdict}")
        assert (await call(get_super_admin, operator))[0] == "ALLOWED"
        assert (await call(get_super_admin, principal))[0].startswith("REFUSED")
        assert (await call(get_super_admin, teacher_a))[0].startswith("REFUSED")

        print()
        print("=" * 78)
        print("4. Is it enforced in code, or only hidden in the interface?")
        print("=" * 78)
        unscoped = []
        for path in sorted((ROOT / "app" / "api" / "routers").glob("*.py")):
            if path.name not in TENANT_ROUTERS:
                continue
            tree = ast.parse(path.read_text())
            ok = total = 0
            for node in ast.walk(tree):
                if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                routed = any(
                    isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
                    for d in node.decorator_list
                )
                if not routed:
                    continue
                total += 1
                args = ast.unparse(node.args)
                if "school_scope" in args or "school_admin_scope" in args:
                    ok += 1
                else:
                    unscoped.append(f"{path.name}:{node.name}")
            print(f"   {path.name:16} {ok}/{total} routed endpoints take a school scope")

        surprises = [name for name in unscoped if name not in UNSCOPED_BY_DESIGN]
        print()
        for name in unscoped:
            if name in UNSCOPED_BY_DESIGN:
                print(f"   exempt: {name} — {UNSCOPED_BY_DESIGN[name]}")
        if surprises:
            print("\n   UNSCOPED AND NOT DECLARED:")
            for name in surprises:
                print(f"     {name}")
        assert not surprises, surprises
        print("\n   Every other endpoint on every tenant router is scoped.")

        print()
        print("ALL BOUNDARY CHECKS PASSED")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
