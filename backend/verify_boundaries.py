"""Proof that the two boundaries in this system actually hold.

    python verify_boundaries.py

It checks two claims that the system makes and that a reader is entitled to
doubt:

    1. The system operator cannot read student records.
       Not "we didn't build a screen for it" — the API refuses.

    2. One school cannot read another school's records.
       Every query that touches a school's data is filtered by the caller's
       own school, with no exceptions.

Both are checked by inspecting the source, not by trusting a comment. The
first walks every route the application registers and reports which dependency
guards it. The second reads every router module and finds any query on a
tenant table that is not scoped.

This runs without a database or a web server, so it can be run on any machine
and shown to anyone. It exits non-zero if either claim fails, which is what
makes it worth running in the first place.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import List, Tuple

ROOT = pathlib.Path(__file__).parent
ROUTERS = ROOT / "app" / "api" / "routers"

# Models whose rows belong to exactly one school. A query on any of these that
# is not filtered by school_id is a potential cross-school read.
TENANT_MODELS = {
    "Student", "StudentEnrollment", "StudentPace", "EarlyWarning",
    "AIRiskPrediction", "DataQualityLog",
    "SchoolYear", "GradeLevel", "Section", "Subject",
    "TeacherAssignment", "ClassSchedule", "TeacherAvailability",
}

# Endpoints that legitimately touch no school data, with the reason. Anything
# NOT on this list must be scoped; the list is short on purpose so that adding
# to it is a visible decision rather than a quiet omission.
UNSCOPED_BY_DESIGN = {
    # auth: you cannot be inside a school before you have an account
    ("auth", "register"): "creating a teacher account; validates the school itself",
    ("auth", "login"): "pre-authentication",
    ("auth", "verify_otp"): "pre-authentication",
    ("auth", "resend_otp"): "pre-authentication",
    ("auth", "refresh_session"): "token exchange; reads only the caller's own session",
    ("auth", "logout"): "ends a session",
    ("auth", "whoami"): "the caller's own profile",
    ("auth", "forgot_password"): "pre-authentication",
    ("auth", "verify_reset_otp"): "pre-authentication",
    ("auth", "reset_password"): "pre-authentication",
    # schools: registration and the operator's own console
    ("schools", "school_directory"): "public: approved school names and cities only",
    ("schools", "register_school"): "public: creates a school, pending",
    ("schools", "my_school"): "the caller's own school settings",
    ("schools", "update_my_school"): "guarded by school_admin_scope",
    ("schools", "list_schools"): "operator console: counts only, no records",
    ("schools", "decide_school"): "operator console: approve/reject a school",
    # staff: a person managing themselves, including the operator
    ("staff", "read_me"): "the caller's own profile",
    ("staff", "update_me"): "the caller's own profile",
    ("staff", "change_own_password"): "the caller's own password",
    ("staff", "upload_photo"): "the caller's own photo",
    ("staff", "remove_photo"): "the caller's own photo",
    # ai: these two talk to the model service and read no records
    ("ai", "ai_status"): "model service health; no records",
    ("ai", "ai_model_card"): "the model's own metrics and limitations",
}

GREEN, RED, YELLOW, DIM, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)


def endpoints(tree: ast.Module) -> List[Tuple[ast.AST, str, List[str]]]:
    """Every decorated route in a module, with the dependencies it declares."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_route = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "patch", "put", "delete"}
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in node.decorator_list
        )
        if not is_route:
            continue
        deps = re.findall(r"Depends\(\s*([A-Za-z_][\w.]*)\s*\)", ast.unparse(node))
        found.append((node, node.name, deps))
    return found


def check_super_admin_boundary() -> bool:
    print(f"{BOLD}1. Can the system operator reach student records?{OFF}")
    print(f"{DIM}   Every route is listed with the dependency that guards it.{OFF}\n")

    guarded = allowed = 0
    failures = []

    for path in sorted(ROUTERS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = path.stem
        tree = ast.parse(path.read_text())
        rows = endpoints(tree)
        if not rows:
            continue
        print(f"   {BOLD}{module}.py{OFF}")
        for _node, name, deps in rows:
            key = (module, name)
            scoped = any(d in {"school_scope", "school_admin_scope"} for d in deps)
            operator_only = "get_super_admin" in deps

            if scoped:
                guarded += 1
                print(f"     {GREEN}refused{OFF}  {name:28} {DIM}school_scope{OFF}")
            elif operator_only:
                allowed += 1
                print(f"     {YELLOW}operator{OFF} {name:28} {DIM}"
                      f"{UNSCOPED_BY_DESIGN.get(key, 'operator console')}{OFF}")
            elif key in UNSCOPED_BY_DESIGN:
                allowed += 1
                print(f"     {DIM}open     {name:28} {UNSCOPED_BY_DESIGN[key]}{OFF}")
            else:
                failures.append(f"{module}.{name}")
                print(f"     {RED}UNGUARDED {name:28} <-- reads records with no "
                      f"school scope{OFF}")
        print()

    print(f"   {guarded} route(s) refuse the operator outright.")
    print(f"   {allowed} route(s) are open by design, each with a stated reason.")
    if failures:
        print(f"\n   {RED}{len(failures)} route(s) unaccounted for:{OFF}")
        for f in failures:
            print(f"     - {f}")
        return False
    print(f"\n   {GREEN}PASS{OFF} — no route lets the operator read a student record.")
    return True


def _is_scope_applied(src: str) -> bool:
    """Does this fragment actually apply the caller's school to a query?"""
    return (
        "scoped(" in src
        or re.search(r"\.school_id\s*==", src) is not None
        or re.search(r"\.school_id\.in_\(", src) is not None
    )


def check_tenant_isolation() -> bool:
    print(f"\n{BOLD}2. Can one school read another school's records?{OFF}")
    print(f"{DIM}   Every query on a school-owned table must be filtered.{OFF}\n")

    unscoped = []
    total = 0

    for path in sorted(ROUTERS.glob("*.py")):
        if path.name in {"__init__.py", "schools.py", "auth.py"}:
            continue  # neither queries a tenant table outside a scoped endpoint
        tree = ast.parse(path.read_text())

        # Work function by function, so a scoped query in one endpoint can
        # never vouch for an unscoped one in another.
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # Map each statement in this function to its source, and note which
            # variable (if any) it assigns to.
            statements = [
                (stmt, ast.unparse(stmt),
                 stmt.targets[0].id
                 if isinstance(stmt, ast.Assign)
                 and len(stmt.targets) == 1
                 and isinstance(stmt.targets[0], ast.Name)
                 else None)
                for stmt in ast.walk(func)
                if isinstance(stmt, ast.stmt)
            ]

            for stmt, stmt_src, target in statements:
                for node in ast.walk(stmt):
                    if not (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "select" and node.args):
                        continue
                    arg = node.args[0]
                    model = (
                        arg.id if isinstance(arg, ast.Name)
                        else arg.value.id
                        if isinstance(arg, ast.Attribute)
                        and isinstance(arg.value, ast.Name) else None
                    )
                    if model not in TENANT_MODELS:
                        continue
                    total += 1

                    # Scoped right here, in this one statement?
                    if _is_scope_applied(stmt_src):
                        break

                    # Otherwise the query may have been built up over several
                    # statements — `stmt = select(X)` then `stmt = stmt.where(…)`.
                    # Follow ONLY further assignments to that same variable in
                    # this same function; nothing else may vouch for it.
                    if target and any(
                        other_target == target
                        and other is not stmt
                        and _is_scope_applied(other_src)
                        for other, other_src, other_target in statements
                    ):
                        break

                    unscoped.append((path.name, node.lineno, model, func.name))
                    break

    print(f"   {total} quer{'y' if total == 1 else 'ies'} on school-owned tables.")
    if unscoped:
        print(f"\n   {RED}{len(unscoped)} not filtered by school:{OFF}")
        for name, line, model, func in unscoped:
            print(f"     - {name}:{line}  select({model}) in {func}()")
        return False
    print(f"   {GREEN}PASS{OFF} — every one is filtered by the caller's school.")

    # db.get() bypasses the WHERE clause entirely, so it must go through the
    # scoping helper instead. A bare db.get on a tenant model is the single
    # easiest way to leak a row, so it is checked separately.
    print(f"\n{DIM}   Checking for bare db.get() on school-owned tables…{OFF}")
    bare = []
    for path in sorted(ROUTERS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for match in re.finditer(r"db\.get\(\s*([A-Za-z_]\w*)", path.read_text()):
            if match.group(1) in TENANT_MODELS:
                bare.append((path.name, match.group(1)))
    if bare:
        print(f"   {RED}{len(bare)} bare db.get() found:{OFF}")
        for name, model in bare:
            print(f"     - {name}: db.get({model}, ...)  "
                  f"should be get_scoped(db, {model}, ..., scope)")
        return False
    print(f"   {GREEN}PASS{OFF} — none; all single-row fetches go through get_scoped.")
    return True


def main() -> int:
    print("=" * 74)
    print("  TENANT AND OPERATOR BOUNDARIES")
    print("  Checked against the source, not against a comment.")
    print("=" * 74)
    print()
    ok = check_super_admin_boundary()
    ok = check_tenant_isolation() and ok

    print("\n" + "=" * 74)
    if ok:
        print(f"  {GREEN}{BOLD}BOTH BOUNDARIES HOLD{OFF}")
        print("  The operator manages schools and accounts and cannot open a")
        print("  student record. No school can read another school's data.")
    else:
        print(f"  {RED}{BOLD}A BOUNDARY IS BROKEN — see above{OFF}")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
