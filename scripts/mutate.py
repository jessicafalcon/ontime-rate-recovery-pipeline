#!/usr/bin/env python3
"""The mutation sweep (the strongest gate findings come from "delete this call —
does the suite notice?"). Run via `make mutate SPEC=…`.

Reads the spec's Invariants section for ONE fenced block:

    ```mutations
    generator/generate.py::emit_events        delete-call
    eval/score.py::label_accuracy             constant-return:1.0
    serving/writeback.py::should_replace       invert-guard
    eval/simulate.py::pick_window             swap-sort-key
    ```

Four Python operators, and (Phase 3) two SQL operators over ONE `case` in a
dbt model, addressed `dbt/models/<…>.sql::<alias>` where `<alias>` is the
column after `end as`:
  drop-arm:<n>        delete the n-th `when … then …` arm (1-based; `else` is
                      not an arm — out of range → ERROR)
  swap-arms:<i>,<j>   exchange arms i and j (precedence)
The SQL suite is the same offline pytest run: tests/test_staging.py builds the
project in-process, so a dbt unit test going red is a KILLED line.

Exactly four Python operators:
  delete-call         remove every statement-level call to the function, in every
                      tracked non-test .py file (a call used as a value is left;
                      none found → ERROR)
  constant-return:<v> the function returns the literal <v> before doing anything
  invert-guard        negate the first `if` test inside the function
  swap-sort-key       reverse the first `sorted(…, key=lambda …: (a, b))` /
                      `.sort(key=…)` / `max(…, key=…)` / `min(…, key=…)` tuple
                      key in the function; else the column list after the first
                      `order by` in a string constant

First the BASELINE: the unmutated suite runs in its own worktree and must be green,
or the sweep refuses (a red HEAD would otherwise print KILLED for every line).
Each mutation: `git worktree add --detach <tmp>/mutate-*/mut-N HEAD` (a temp dir
from `tempfile.mkdtemp`, which honours TMPDIR; the working tree is never touched;
the mutation is applied to HEAD), the
offline suite runs there with THIS interpreter and a REDUCED environment
(`review_common.suite_env`: PATH, HOME, PYTHONPATH, OTR_INT — never credentials),
the worktree is removed in a `finally` that also compares `git worktree list`
before/after (stale ones pruned at start). Verdicts: KILLED / SURVIVED / ERROR, one
per mutation + file:line, summing to the mutation count; a registry change is a
separate latched REGISTRY line, reported once. Exit 1 on any survivor, error or
registry change. Uses ast/subprocess/pathlib only. Not a pytest file."""

from __future__ import annotations

import argparse
import ast
import posixpath
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_common import (  # noqa: E402
    ROOT,
    Refused,
    die,
    resolve_spec,
    run,
    section,
    suite_env,
)

OPERATORS = ("delete-call", "constant-return", "invert-guard", "swap-sort-key")
SQL_OPERATORS = ("drop-arm", "swap-arms")
_ARM_ARG = {
    "drop-arm": re.compile(r"^[1-9]\d*$"),
    "swap-arms": re.compile(r"^[1-9]\d*,[1-9]\d*$"),
}
MAX_LITERAL = 64  # chars; `constant-return:<v>` is a small literal, nothing else
_BLOCK = re.compile(r"```mutations\n(.*?)```", re.S)
_ORDER_BY = re.compile(r"(order by\s+)([^\n;)]+?)(\s*(?:limit\b|$))", re.I | re.M)
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-x",
    "-p",
    "no:cacheprovider",
    "--ignore=tests/integration",
]


@dataclass(frozen=True)
class Mutation:
    file: str
    func: str
    op: str
    arg: str = ""

    def __str__(self) -> str:
        op = f"{self.op}:{self.arg}" if self.arg else self.op
        return f"{self.file}::{self.func} {op}"


def parse_mutations(spec_text: str) -> list[Mutation]:
    m = _BLOCK.search(section(spec_text, "Invariants"))
    if not m:
        raise Refused(
            "refusing: the spec's Invariants section has no ```mutations block — "
            "add `## Invariants` with one (shape: specs/TEMPLATE.md)"
        )
    out: list[Mutation] = []
    for ln in m.group(1).splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split(
            None, 1
        )  # `path::func` then the operator (a literal may hold spaces)
        if len(parts) == 2:
            parts[1] = parts[1].strip()
        if len(parts) != 2 or "::" not in parts[0] or " " in parts[0]:
            raise Refused(
                f"refusing: bad mutations line (want `path.py::func op`): {ln!r}"
            )
        file, func = parts[0].split("::", 1)
        op, _, arg = parts[1].partition(":")
        if op in SQL_OPERATORS:
            if not _ARM_ARG[op].match(arg):
                raise Refused(f"refusing: {op} takes {_ARM_ARG[op].pattern!r}: {ln!r}")
            out.append(Mutation(_model_path(file), func, op, arg))
            continue
        if op not in OPERATORS or (op == "constant-return") != bool(arg):
            raise Refused(
                f"refusing: unknown operator {parts[1]!r} "
                f"(one of {', '.join(OPERATORS)})"
            )
        if arg:
            _literal_or_refuse(arg)
        out.append(Mutation(_repo_path(file), func, op, arg))
    if not out:
        raise Refused("refusing: the mutations block is empty")
    return out


def _repo_path(file: str) -> str:
    """A mutation target, resolved ONCE and gated on `Path.parts`, never on a
    string prefix (DECISIONS "Process", the model-text invariant): `./tests/x.py`,
    `tests/../eval/x.py` and `eval/./x.py` all normalize first (posixpath, so the
    rule is platform-independent), then the rules — relative, no `..`, `.py`, not
    under tests/ (casefolded: macOS is case-insensitive) — apply to the parts."""
    parts = PurePosixPath(posixpath.normpath(file)).parts
    if not parts or Path(file).is_absolute() or ".." in parts or parts[0] == "..":
        raise Refused(
            f"refusing: mutation target must be a repo-relative path: {file!r}"
        )
    if not parts[-1].endswith(".py"):
        raise Refused(f"refusing: mutation target must be a .py file: {file!r}")
    if parts[0].casefold() == "tests":  # the filesystem may not be case-sensitive
        raise Refused(
            f"refusing: {file!r} is under tests/ — the sweep never mutates the "
            "oracle it judges by (every operator, not only delete-call)"
        )
    return "/".join(parts)


def _model_path(file: str) -> str:
    """A SQL mutation target: a tracked `.sql` under dbt/models/ (the models are
    the only SQL the suite builds; macros and tests are the oracle's side)."""
    parts = PurePosixPath(posixpath.normpath(file)).parts
    if not parts or Path(file).is_absolute() or ".." in parts or parts[0] == "..":
        raise Refused(
            f"refusing: mutation target must be a repo-relative path: {file!r}"
        )
    if parts[:2] != ("dbt", "models") or not parts[-1].endswith(".sql"):
        raise Refused(
            f"refusing: a SQL mutation target must be dbt/models/**.sql: {file!r}"
        )
    return "/".join(parts)


def _literal_or_refuse(arg: str) -> None:
    """`<v>` is written into a source file that the sweep's pytest then imports —
    so it must be a Python LITERAL (`0`, `None`, `'x'`, `(1, 2)`), never an
    expression. Spec text is model-authored; it never reaches exec."""
    if len(arg) > MAX_LITERAL:
        raise Refused(
            f"refusing: constant-return value longer than {MAX_LITERAL} chars"
        )
    try:
        ast.literal_eval(arg)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        raise Refused(
            f"refusing: constant-return value is not a literal: {arg!r}"
        ) from None


# -------------------------------------------------------- text surgery


class Source:
    """Line-level edits guided by ast spans, so everything around the mutation
    keeps its formatting (ast.unparse of a whole file would reformat it)."""

    def __init__(self, path: Path, tree: Path) -> None:
        # A tracked symlink could point out of the worktree; the write must not.
        if tree.resolve() not in path.resolve().parents:
            raise Refused(f"refusing: {path.name} resolves outside the worktree")
        self.path = path
        self.text = path.read_text()
        self.lines = self.text.splitlines(keepends=True)
        self.tree = ast.parse(self.text)

    def function(self, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
        for node in ast.walk(self.tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return node
        raise Refused(f"refusing: no function {name!r} in {self.path.name}")

    def replace_span(self, node: ast.AST, new: str) -> int:
        """Replace node's source span with `new`; returns the first line (1-based)."""
        l0, c0, l1, c1 = (
            node.lineno,
            node.col_offset,
            node.end_lineno,
            node.end_col_offset,
        )
        head = self.lines[l0 - 1][:c0]
        tail = self.lines[l1 - 1][c1:]
        self.lines[l0 - 1 : l1] = [head + new + tail]
        return l0

    def delete_stmt(self, node: ast.stmt) -> int:
        indent = self.lines[node.lineno - 1][: node.col_offset]
        self.lines[node.lineno - 1 : node.end_lineno] = [indent + "pass  # MUTATED\n"]
        return node.lineno

    def write(self) -> None:
        self.path.write_text("".join(self.lines))


def _calls(name: str) -> bool:
    def pred(node: ast.AST) -> bool:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            return False
        f = node.value.func
        return (isinstance(f, ast.Name) and f.id == name) or (
            isinstance(f, ast.Attribute) and f.attr == name
        )

    return pred


_WHEN = re.compile(r"^\s*when\b", re.I | re.M)
_ELSE = re.compile(r"^\s*else\b", re.I | re.M)


def sql_arms(text: str, alias: str) -> tuple[int, int, list[str], str]:
    """Locate `case … end as <alias>`: returns (start, end) offsets of the text
    between `case` and `end`, the `when…then…` arms (each a whole line block),
    and the trailing `else …` block. One arm per line-leading `when`."""
    end = re.search(rf"^\s*end\s+as\s+{re.escape(alias)}\b", text, re.I | re.M)
    if not end:
        raise Refused(f"refusing: no `end as {alias}` in the model")
    start = None
    for mm in re.finditer(r"^\s*case\b[^\n]*\n", text, re.I | re.M):
        if mm.end() <= end.start():
            start = mm.end()
    if start is None:
        raise Refused(f"refusing: no `case` before `end as {alias}`")
    body = text[start : end.start()]
    whens = [mm.start() for mm in _WHEN.finditer(body)]
    if not whens:
        raise Refused(f"refusing: no `when` arm in the `case … end as {alias}`")
    els = _ELSE.search(body)
    tail = els.start() if els else len(body)
    bounds = whens + [tail]
    arms = [body[a:b] for a, b in zip(bounds, bounds[1:], strict=False)]
    return start, end.start(), arms, body[tail:]


def _apply_sql(m: Mutation, tree: Path) -> str:
    path = tree / m.file
    if tree.resolve() not in path.resolve().parents:
        raise Refused(f"refusing: {path.name} resolves outside the worktree")
    text = path.read_text()
    start, end, arms, rest = sql_arms(text, m.func)
    idx = [int(x) for x in m.arg.split(",")]
    for i in idx:
        if i > len(arms):
            raise Refused(f"refusing: {m.op}:{m.arg} — the case has {len(arms)} arms")
    if m.op == "drop-arm":
        line = text[: start + sum(len(a) for a in arms[: idx[0] - 1])].count("\n") + 1
        del arms[idx[0] - 1]
    else:
        i, j = idx
        if i == j:
            raise Refused("refusing: swap-arms needs two different arms")
        line = (
            text[: start + sum(len(a) for a in arms[: min(i, j) - 1])].count("\n") + 1
        )
        arms[i - 1], arms[j - 1] = arms[j - 1], arms[i - 1]
    path.write_text(text[:start] + "".join(arms) + rest + text[end:])
    return f"{m.file}:{line}"


def apply(m: Mutation, tree: Path) -> str:
    """Apply `m` inside worktree `tree`; return `file:line` of the edit."""
    if m.op in SQL_OPERATORS:
        code, _ = run(["git", "ls-files", "--error-unmatch", "--", m.file], tree)
        if code != 0:
            raise Refused(f"refusing: {m.file} is not a tracked file")
        return _apply_sql(m, tree)
    if m.op == "delete-call":
        pred = _calls(m.func)
        hits: list[str] = []
        _, files = run(["git", "ls-files", "-z", "--", "*.py"], tree)
        for rel in files.split("\0"):
            if not rel:
                continue
            if rel.startswith("tests/"):
                continue
            src = Source(tree / rel, tree)
            stmts = [n for n in ast.walk(src.tree) if pred(n)]
            for n in sorted(
                stmts, key=lambda n: -n.lineno
            ):  # bottom-up keeps spans valid
                hits.append(f"{rel}:{src.delete_stmt(n)}")
            if stmts:
                src.write()
        if not hits:
            raise Refused(
                f"refusing: no statement-level call to {m.func}() outside tests/"
            )
        return ",".join(sorted(hits))
    code, _ = run(["git", "ls-files", "--error-unmatch", "--", m.file], tree)
    if code != 0:
        raise Refused(f"refusing: {m.file} is not a tracked file")
    src = Source(tree / m.file, tree)
    fn = src.function(m.func)
    if m.op == "constant-return":
        first = fn.body[0]
        indent = src.lines[first.lineno - 1][: first.col_offset]
        src.lines.insert(first.lineno - 1, f"{indent}return {m.arg}  # MUTATED\n")
        line = first.lineno
    elif m.op == "invert-guard":
        ifs = [n for n in ast.walk(fn) if isinstance(n, ast.If)]
        if not ifs:
            raise Refused(f"refusing: no `if` inside {m.func}()")
        test = min(ifs, key=lambda n: (n.lineno, n.col_offset)).test
        line = src.replace_span(test, f"not ({ast.unparse(test)})")
    elif m.op == "swap-sort-key":
        line = _swap_sort_key(src, fn, m)
    else:  # pragma: no cover — parse_mutations rejects anything else
        raise Refused(f"refusing: unknown operator {m.op}")
    src.write()
    return f"{m.file}:{line}"


def _swap_sort_key(src: Source, fn: ast.AST, m: Mutation) -> int:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        is_sort = (isinstance(f, ast.Name) and f.id in ("sorted", "max", "min")) or (
            isinstance(f, ast.Attribute) and f.attr == "sort"
        )
        if not is_sort:
            continue
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Lambda):
                body = kw.value.body
                if isinstance(body, ast.Tuple) and len(body.elts) > 1:
                    swapped = ast.Tuple(elts=list(reversed(body.elts)), ctx=ast.Load())
                    return src.replace_span(body, ast.unparse(swapped))
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            mm = _ORDER_BY.search(node.value)
            if mm:
                cols = [c.strip() for c in mm.group(2).split(",")]
                if len(cols) > 1:
                    new = (
                        node.value[: mm.start(2)]
                        + ", ".join(reversed(cols))
                        + node.value[mm.end(2) :]
                    )
                    return src.replace_span(node, repr(new))
    raise Refused(
        f"refusing: no multi-key sorted()/sort()/`order by` inside {m.func}()"
    )


# -------------------------------------------------------------- worktree


def _registry_changed(before: str, after: str) -> bool:
    """`git status` cannot see .git/worktrees; this can (invariant: registry
    restored)."""
    return after != before


def _baseline_is_green(root: Path, scratch: Path, suite: list[str]) -> bool:
    """Run the UNMUTATED suite in its own worktree (invariant: sweep baseline).
    Red here means every later KILLED would be meaningless."""
    tree = scratch / "baseline"
    try:
        code, _ = run(
            ["git", "worktree", "add", "--detach", "-q", str(tree), "HEAD"], root
        )
        if code != 0:
            return False
        code, _ = run(suite, tree, env=suite_env(tree))
        return code == 0
    finally:
        run(["git", "worktree", "remove", "--force", str(tree)], root)
        run(["git", "worktree", "prune"], root)


def sweep(
    mutations: list[Mutation], root: Path, scratch: Path, suite: list[str]
) -> int:
    """Run every mutation; print one line each; return the exit code."""
    survivors = 0
    errors = 0
    registry_changed = False  # latched: its own outcome, never a mutation verdict
    run(["git", "worktree", "prune"], root)  # a SIGKILLed earlier sweep leaves one
    _, before = run(["git", "worktree", "list"], root)
    if not _baseline_is_green(root, scratch, suite):
        raise Refused(
            "refusing: the unmutated suite is red at HEAD — fix the suite before "
            "judging mutations (a red baseline would print KILLED for every line)"
        )
    for i, m in enumerate(mutations, 1):
        tree = scratch / f"mut-{i}"
        try:
            code, out = run(
                ["git", "worktree", "add", "--detach", "-q", str(tree), "HEAD"], root
            )
            if code != 0:
                print(
                    f"ERROR    {m}: git worktree add failed: "
                    f"{out.strip().splitlines()[-1]}"
                )
                errors += 1
                continue
            try:
                where = apply(m, tree)
            except (Refused, SyntaxError, OSError) as e:
                print(f"ERROR    {m}: {e}")
                errors += 1
                continue
            code, _ = run(suite, tree, env=suite_env(tree))
            verdict = "KILLED  " if code != 0 else "SURVIVED"
            survivors += code == 0
            print(f"{verdict} {m} -> {where}")
        finally:
            run(["git", "worktree", "remove", "--force", str(tree)], root)
            run(["git", "worktree", "prune"], root)
            _, after = run(["git", "worktree", "list"], root)
            if _registry_changed(before, after) and not registry_changed:
                # `git status` cannot see .git/worktrees; this can. Reported once.
                print(f"REGISTRY worktree registry changed after {m}:\n{after}")
                registry_changed = True
    killed = len(mutations) - survivors - errors  # the triple always sums to len
    assert killed >= 0
    bad = survivors + errors + registry_changed
    print(
        f"mutate {'FAIL' if bad else 'OK'}: {killed}/{len(mutations)} killed, "
        f"{survivors} survived, {errors} errors"
        + (", worktree registry changed" if registry_changed else "")
    )
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--spec", required=True, help="specs/<file>.md with a ```mutations block"
    )
    a = ap.parse_args(argv)
    try:
        spec = resolve_spec(a.spec)
        mutations = parse_mutations(spec.read_text())
    except Refused as e:
        die(str(e))
    scratch = Path(tempfile.mkdtemp(prefix="mutate-"))  # honours TMPDIR
    try:
        return sweep(mutations, ROOT, scratch, SUITE)
    except Refused as e:
        die(str(e))
    finally:
        try:
            scratch.rmdir()  # empty once every worktree was removed
        except OSError:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # one line, never a traceback
        die(f"mutate error: {type(e).__name__}: {e}", 1)
