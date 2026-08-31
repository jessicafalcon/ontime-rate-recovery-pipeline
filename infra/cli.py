"""`make tf-<cmd> [PROJECT=<id>] [CONFIRM=yes]`, cmd one of validate | plan | apply |
destroy | freeze.

One entry point validates PROJECT (a GCP project-id shape) before deriving the
`-var`, then runs terraform with `-chdir=infra` (a fixed dir, never user input):
tf-validate — offline: `init -backend=false -lockfile=readonly` + `validate` +
              `fmt -check`. No auth; the pinned lock is never rewritten.
tf-plan     — reads GCP APIs (ADC/WIF); shows the diff. Non-destructive.
tf-apply    — creates cloud resources. CONFIRM=yes from the command line only.
              Plans FIRST (`plan -out`), reads the plan back (`show -json`) and
              applies it only if every planned action is inside SAFE_ACTIONS —
              `delete` needs ALLOW_DESTROY=yes with command-line origin, any
              other verb or an unreadable plan is refused always (Phase 10
              review round 2 #3, round 4 Amendment N1): an apply that omits a
              toggle (`enable_spanner=true` while Spanner is up) would otherwise
              be a silent teardown. The saved plan is what gets applied: the
              plan you were shown is the apply you get.
tf-destroy  — deletes them. CONFIRM=yes from the command line only.
tf-freeze   — rewrites infra/MANIFEST.sha256 (the content pin over every file
              Terraform loads — `*.tf`, `*.tf.json` — plus the provider lock;
              Amendments P/R). CONFIRM=yes from the command line only: it is
              the ONLY writer of the manifest.
plan/apply/destroy refuse while an auto-loaded `terraform.tfvars` /
`*.auto.tfvars{,.json}` sits under infra/ (Amendment T): a toggle reaches
Terraform only as a command-line `-var`, never from a gitignored file.

Auth is ADC (local `gcloud auth application-default login`) or WIF (CI) — no
keyfile, no secret. tf-apply/tf-destroy are cloud-cost/destructive: ask first.

`tf()` takes an injectable `runner` (default `subprocess.run`) so the offline
tests exercise the guards against a fake — a real terraform is never spawned by
`make test`/`make mutate` even if a guard is mutated away (review round 1)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from generator import manifest as _manifest

INFRA_DIR = Path(__file__).parent
MANIFEST = INFRA_DIR / _manifest.NAME
# GCP project id: 6–30 chars, a lowercase letter first, then lowercase letters /
# digits / hyphens, not ending in a hyphen. `\Z` (not `$`) so a trailing newline
# is rejected.
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
# apply/destroy touch the cloud (apply is cost, destroy is destructive); both are
# gated on CONFIRM=yes from the command line.
CLOUD_MUTATING = ("apply", "destroy")
# tf-freeze overwrites the committed manifest: same gate, same origin rule.
CONFIRM_GATED = CLOUD_MUTATING + ("freeze",)
_TF = ["terraform", f"-chdir={INFRA_DIR}"]


def die(msg: str, code: int = 2) -> NoReturn:
    print(msg)
    sys.exit(code)


def validate_project(project: str) -> str:
    if not PROJECT_RE.match(project):
        die(
            "PROJECT: refused — want a GCP project id "
            f"[a-z][a-z0-9-]{{4,28}}[a-z0-9], got {project!r}"
        )
    return project


def confirmed(confirm: str, origin: str) -> bool:
    """THE rule: a `yes` with command-line origin (`$(origin <VAR>)`). ONE
    predicate for every gate in the pipeline CLIs — CONFIRM on the tf-*
    targets, landing.cli's/pipeline.cli's cloud commands and drop-db, the
    integration fixtures' carried gate, ALLOW_DESTROY on tf-apply and FULL on
    dbt-build (round 2 #7, round 3 #4, rounds 4–5) — so none can drift from it.
    `make freeze` (generator/cli.py) keeps its own literal: the generator does
    not import the pipeline. It lives beside the cloud-env policy because the
    pipeline CLIs import from here (the reverse would cycle)."""
    return origin == "command line" and confirm == "yes"


def require_confirm(cmd: str, confirm: str, origin: str) -> None:
    """Cloud-cost / destructive: CONFIRM=yes must have command-line origin
    ($(origin CONFIRM)); an env-set CONFIRM=yes is refused (CLAUDE.md: ask
    first, every time)."""
    if not confirmed(confirm, origin):
        die(f"tf-{cmd}: refused — pass CONFIRM=yes on the command line")


PINNED_SUFFIXES = (".tf", ".tf.json")


def is_pinned(p: Path) -> bool:
    """The manifest's predicate: every file Terraform loads — `.tf` AND
    `.tf.json` (round 7 #3) — plus the provider lock, pruning `.terraform/`
    (the gitignored provider cache). `*.tfvars`, `*.tfstate*` and `*.example`
    are outside by construction."""
    if ".terraform" in p.relative_to(INFRA_DIR).parts:
        return False
    return p.name.endswith(PINNED_SUFFIXES) or p.name == ".terraform.lock.hcl"


def compute_manifest() -> dict[str, str]:
    return _manifest.compute(INFRA_DIR, is_pinned)


def manifest_diff() -> list[str]:
    """`<path>: missing|extra|changed` per drifted entry via the fixtures'
    `generator.manifest.diff` (one implementation, round 7 #6); empty when the
    tree matches the committed manifest (the offline test's assertion). A
    missing manifest reads as every pinned file `extra`."""
    if not MANIFEST.is_file():
        return [f"{k}: extra" for k in sorted(compute_manifest())]
    return _manifest.diff(INFRA_DIR, MANIFEST, is_pinned)


def freeze(confirm: str, origin: str) -> int:
    """Rewrite the pin from disk — refusing, like `make freeze`, when a path the
    committed manifest lists has vanished (a deleted `.tf` must be an explicit
    delete, never a silent narrowing of the allowlist — round 7 #5)."""
    require_confirm("freeze", confirm, origin)
    m = compute_manifest()
    if MANIFEST.is_file():
        gone = sorted(set(_manifest.parse(MANIFEST.read_text())) - set(m))
        if gone:
            die(
                "tf-freeze: refused — pinned files missing on disk: "
                + ", ".join(gone)
                + " (delete them from the manifest by hand in the same commit)"
            )
    MANIFEST.write_text(_manifest.render(m))
    print(f"tf-freeze OK: {len(m)} files pinned in {MANIFEST.name}")
    return 0


AUTO_TFVARS = re.compile(r"\A(terraform\.tfvars|.*\.auto\.tfvars)(\.json)?\Z")


def auto_tfvars() -> list[str]:
    """Files Terraform loads without being asked (`terraform.tfvars`,
    `*.auto.tfvars`, `*.auto.tfvars.json`) directly under the chdir — gitignored
    and unpinned, so outside every other control (Amendment T)."""
    return sorted(p.name for p in INFRA_DIR.iterdir() if AUTO_TFVARS.match(p.name))


# One item: `name=scalar` (no whitespace, no comma — one item is one `-var`) or
# `name=[n,n,…]` (a bracketed numeric list, the shape of `budget_alert_thresholds`).
VAR_ITEM_RE = re.compile(
    r"^([a-z][a-z0-9_]*)=(?:[^,\s\[\]]+|\[[0-9.]+(?:,[0-9.]+)*\])\Z"
)
# Items are split on a comma that is NOT inside brackets.
_ITEM_SPLIT = re.compile(r",(?![^\[]*\])")


def parse_vars(vars_: str, origin: str = "") -> list[str]:
    """`VARS='k=v,k2=[50,150]'` → `['-var', 'k=v', '-var', 'k2=[50,150]']`
    (fix/tf-vars-argv). The ONLY way a toggle reaches Terraform, and only from
    the command line (`$(origin VARS)`, like CONFIRM — an exported VARS would
    toggle a typed apply with nothing in the typed line); `project_id` is
    PROJECT's alone. Empty → no `-var` beyond project_id. `origin` defaults to
    the REFUSING `""` (round 6 #4): a caller with a non-empty VARS must state
    the origin, like `confirmed`; only `main()` (from `$(origin VARS)`) does."""
    if not vars_:
        return []
    if origin != "command line":
        die(f"VARS: refused — set on the command line, not the {origin}")
    out: list[str] = []
    for item in _ITEM_SPLIT.split(vars_):
        m = VAR_ITEM_RE.match(item)
        if not m:
            die(f"VARS: refused — want name=value items joined by ',', got {item!r}")
        if m.group(1) == "project_id":
            die("VARS: refused — project_id comes from PROJECT, not VARS")
        out += ["-var", item]
    return out


# The Google environment namespace (Amendment N2, round 4; its DOMAIN closed
# by O1, round 5; widened over what the installed libraries READ by P1, round
# 6; narrowed to a DECLARED CLOSED SET by Amendment Q, the security re-review
# of P): every variable a google client acts on is a setting, a credential, an
# identity redirection (an emulator host makes a client use anonymous
# credentials against a named host; a metadata host issues the token), or a
# transport redirection (a proxy endpoint, a TLS trust-anchor override, a
# session-key log). The policy is an ALLOWLIST: a cloud command runs only while
# every name in the domain is one of the settings in CLOUD_ENV_ALLOW; any other
# name refuses LOUDLY (names only, never values) before a client or child
# exists. The refuse domain is ENUMERATED and pinned — the vendor prefixes, the
# _EMULATOR_HOST suffix, the prefix-less names the libraries read
# (CLOUD_ENV_NAMES) and the transport-redirection class (REDIRECTION_NAMES) —
# so it refuses on the IN-PROCESS cloud paths (bq-load, writeback), not only
# the terraform child (P2 closed the redirection class for the child alone).
# tests/test_infra.py's closure test pins the PREFIXES, the suffix and
# CLOUD_ENV_ALLOW exactly (an exact `==` pin on REDIRECTION_NAMES /
# CLOUD_ENV_NAMES membership is BACKLOG), and checks every name the vendor
# DECLARATION modules name is classified once. It also scans the installed
# google/ tree for literal env reads and HARD-FAILS on any newly-read name not
# classified — so a library upgrade that adds a LITERAL-keyed read reddens the
# suite; only a CONSTANT-keyed read escapes the scan (Q A3 records the two
# known — APPDATA, refused, and ENABLE_GCS_PYTHON_CLIENT_OTEL_TRACES, a benign
# tracing switch). The scan does not prove the domain closed over everything
# the libraries read; the ENUMERATED refuse domain is the security boundary. A
# false refusal is the intended direction; admitting a setting is one line here
# plus a DECISIONS entry.
CLOUD_ENV_PREFIXES = (
    "GOOGLE_",
    "GCLOUD_",
    "CLOUDSDK_",
    "GCE_METADATA_",
    # the spanner client's own settings namespace (P1): emulator host,
    # optimizer, metrics and tracing switches all live under it, and a new
    # one lands refused instead of unseen
    "SPANNER_",
)
CLOUD_ENV_SUFFIXES = ("_EMULATOR_HOST",)
# Prefix-less inputs the installed libraries read (google-auth's GCE/App
# Engine detection switches, storage's endpoint/version overrides,
# datastore's dataset; P1: google-genai — a locked dbt-bigquery transitive —
# reads an API key; Q: APPDATA is the Windows ADC config root, the identity
# class, CLOUDSDK_CONFIG's sibling). The TLS trust-anchor overrides moved to
# REDIRECTION_NAMES.
CLOUD_ENV_NAMES = frozenset(
    {
        "NO_GCE_CHECK",
        "APPENGINE_RUNTIME",
        "API_ENDPOINT_OVERRIDE",
        "API_VERSION_OVERRIDE",
        "DATASTORE_DATASET",
        "GEMINI_API_KEY",  # an API key: a credential's value, never in the env
        "APPDATA",  # Windows ADC config root — identity, refused (Q, A3)
    }
)
# The transport-redirection class the Credential standard names a secret: a
# proxy endpoint, a TLS trust-anchor override, a gRPC roots override, a
# session-key log, an OAuth downgrade. Refused on the IN-PROCESS cloud paths,
# not only the terraform child ENV_ALLOW governs (Amendment Q, A1). The CA /
# TLS / key-log names are a finite closed set. The PROXY names are enumerated
# by spelling — a known RESIDUAL: `requests`/`urllib` honor any name whose
# casefold ends `_proxy` (mixed-case variants still pass), and this set is not
# itself `==`-pinned. Both are BACKLOG (upgrade the proxy half to the
# casefold-`_proxy` predicate + an exact membership pin, and add the
# in-process-entry-point refusal test). `grpc_proxy` (grpc's own, honored on
# the Spanner write-back path) is added so that concrete redirect is closed
# today.
REDIRECTION_NAMES = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "grpc_proxy",  # grpc C-core proxy — the Spanner write-back's transport
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
        "SSLKEYLOGFILE",
        "OAUTHLIB_INSECURE_TRANSPORT",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
)
# Read by installed google-namespace code but NOT an input of any cloud
# client on a path this project can reach; refusing them would be a false
# refusal on an unrelated tool's (or the test runner's own) variables.
# Classified here, each class with its reason, so the closure test can
# demand that every vendor-read name is accounted for.
CLOUD_ENV_IGNORED_PREFIXES = (
    "AWS_",  # google-auth: an AWS external-account ADC file — no path here
    "AIP_",  # aiplatform: set BY Vertex inside its managed training containers
    "CLOUD_ML_",  # aiplatform: legacy Vertex job metadata, same containers
    "VERTEX_",  # aiplatform: prediction-server tuning knobs, same containers
)
CLOUD_ENV_IGNORED = frozenset(
    {
        # aiplatform's prediction model server (runs only in a Vertex container)
        "HANDLER_CLASS",
        "HANDLER_MODULE",
        "PREDICTOR_CLASS",
        "PREDICTOR_MODULE",
        "WEB_CONCURRENCY",
        "LIT_PROXY_URL",
        # CI/test markers read by vendored test helpers, not client inputs
        # (PYTEST_CURRENT_TEST is set by pytest itself DURING the suite)
        "GITHUB_ACTIONS",
        "PYTEST_CURRENT_TEST",
        "UNITTEST_ON_FORGE",
        # protobuf runtime switches — an implementation choice, not identity
        "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION",
        "TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK",
    }
)
# The settings the runbook uses (O3: the impersonation SETTING is not
# admitted — the runbook impersonates with the login flag, and a selector of
# WHO acts is not a setting; nothing here spawns gcloud, so its interpreter
# path is not admitted either). P4 (round 6 #6): CLOUDSDK_CONFIG is
# identity-BEARING — it selects the directory the ADC file lives in, i.e.
# which credential every google client and the terraform child use — and is
# accepted anyway: ADC must live somewhere, and HOME (outside the domain by
# construction, inside ENV_ALLOW) redirects it identically, so refusing it
# removes nothing. The other two are project defaults, not identities.
CLOUD_ENV_ALLOW = frozenset(
    {
        "CLOUDSDK_CONFIG",  # the ADC config dir — identity-bearing, accepted (P4)
        "CLOUDSDK_CORE_PROJECT",  # a project default, not an identity
        "GOOGLE_CLOUD_PROJECT",  # a project default, not an identity
    }
)
# What the terraform child may see (fix/tf-vars-argv): the argv is the whole
# input BY CONSTRUCTION — no TF_VAR_*, TF_CLI_ARGS*, credential variable,
# TF_WORKSPACE, TF_DATA_DIR or TF_LOG* can reach it. Auth is the ADC file in
# CLOUDSDK_CONFIG / HOME; the rest is process hygiene. A name here must not
# be one the gate refuses (pinned through in_cloud_namespace): the child
# never sees a refused name. P2 (round 6 #3) dropped SSL_CERT_FILE /
# NO_PROXY / HTTPS_PROXY — an operator-suppliable proxy endpoint plus
# trust-anchor override on every Google API call the provider makes is the
# endpoint-redirection class the Credential standard names a secret; the
# runbook's network is direct, and a proxied one is a deliberate widening
# (one line + a DECISIONS entry), not a default.
ENV_ALLOW = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "CLOUDSDK_CONFIG",
    "CLOUDSDK_CORE_PROJECT",
)
ENV_REFUSE_PREFIXES = ("TF_VAR_", "TF_CLI_ARGS")


def tf_env() -> dict[str, str]:
    """The allowlisted environment every terraform child gets."""
    return {k: v for k, v in os.environ.items() if k in ENV_ALLOW}


def env_tf_vars() -> list[str]:
    """`TF_VAR_*` / `TF_CLI_ARGS*` names in this process's environment —
    Terraform would read them unasked with nothing in the argv showing it. The
    allowlist already drops them; this names them so the refusal is loud."""
    return sorted(k for k in os.environ if k.startswith(ENV_REFUSE_PREFIXES))


def in_cloud_namespace(name: str) -> bool:
    """The refused DOMAIN: a vendor prefix, the emulator-host suffix, a
    prefix-less name the libraries read (O1), or a name in the
    transport-redirection class (Amendment Q — refused on every path)."""
    return (
        name.startswith(CLOUD_ENV_PREFIXES)
        or name.endswith(CLOUD_ENV_SUFFIXES)
        or name in CLOUD_ENV_NAMES
        or name in REDIRECTION_NAMES
    )


def unlisted_cloud_env(env: dict[str, str] | None = None) -> list[str]:
    """Names in the cloud-env domain that CLOUD_ENV_ALLOW does not admit —
    what the gate refuses and what tests/conftest.py scrubs (one function).
    `env` is the mapping to inspect; None means this process's environment
    (an empty mapping is empty, never a fallback)."""
    src = os.environ if env is None else env
    return sorted(k for k in src if in_cloud_namespace(k) and k not in CLOUD_ENV_ALLOW)


def refuse_cloud_env(what: str) -> None:
    """The ONE cloud-env policy for every cloud command (tf-plan/apply/destroy,
    bq-load, spanner-load, dbt-build on a cloud target, the write-back's
    Spanner target, test-int-*): an unlisted Google-namespace variable in the
    environment is a refusal, before any client or child exists."""
    found = unlisted_cloud_env()
    if found:
        die(
            f"{what}: refused — {', '.join(found)} in the environment is not on "
            "the cloud-env allowlist (infra.cli.CLOUD_ENV_ALLOW); a domain name "
            "that is not a listed setting is a credential, an identity, or an "
            "endpoint/trust-anchor redirection — unset it (auth is ADC, never a key)"
        )


def refuse_env_tf_vars(cmd: str) -> None:
    found = env_tf_vars()
    if found:
        die(
            f"tf-{cmd}: refused — {', '.join(found)} in the environment would reach "
            "Terraform unseen; unset it and pass VARS='name=value,…' instead"
        )


def refuse_auto_tfvars(cmd: str) -> None:
    found = auto_tfvars()
    if found:
        die(
            f"tf-{cmd}: refused — infra/{', infra/'.join(found)} auto-loads; "
            "delete it and pass VARS='name=value,…' on the command line (Amendment T)"
        )


Runner = Callable[..., subprocess.CompletedProcess]


# The plan-action verbs a saved plan may carry and still apply (Amendment N1,
# round 4): `terraform show -json`'s `resource_changes[].change.actions`. An
# ALLOWLIST — `delete` (a replace is `["delete","create"]`: a delete) is
# admitted only by ALLOW_DESTROY=yes from the command line; ANY other verb
# (`forget`: a state drop that leaves the resource billing with no teardown
# path; a verb a later Terraform adds) refuses always. A shape we do not
# recognise is not safe.
SAFE_ACTIONS = frozenset({"no-op", "read", "create", "update"})
DELETE = "delete"
_UNREADABLE = (
    "tf-apply: refused — the saved plan could not be read back ({}); nothing applied"
)


def planned_changes(show_json: str) -> list[tuple[str, frozenset[str]]]:
    """`(address, actions)` per resource change of a saved plan. STRICT: an
    empty, non-JSON or non-object body, a missing `resource_changes` list, or
    an entry that is not `{address: str, change: {actions: [str, …]}}` with at
    least one action is a refusal, never "no changes" (round 3 K's envelope
    checks, round 4 N1's per-entry ones, round 5 O2's non-empty rule — in one
    place; the gate runs on evidence, and an empty set is not evidence)."""
    try:
        plan = json.loads(show_json)
    except ValueError as e:  # JSONDecodeError; an empty body lands here too
        die(_UNREADABLE.format(e))
    changes = plan.get("resource_changes") if isinstance(plan, dict) else None
    if not isinstance(changes, list):
        die(_UNREADABLE.format("show -json has no resource_changes list"))
    out: list[tuple[str, frozenset[str]]] = []
    for i, rc in enumerate(changes):
        address = rc.get("address") if isinstance(rc, dict) else None
        change = rc.get("change") if isinstance(rc, dict) else None
        actions = change.get("actions") if isinstance(change, dict) else None
        if (
            not isinstance(address, str)
            or not isinstance(actions, list)
            or not actions
            or not all(isinstance(a, str) for a in actions)
        ):
            die(_UNREADABLE.format(f"resource_changes[{i}] has no address/actions"))
        out.append((address, frozenset(actions)))
    return out


def unsafe_changes(
    changes: list[tuple[str, frozenset[str]]], allowed: frozenset[str]
) -> list[str]:
    """Addresses whose action set is not inside `allowed`."""
    return sorted(address for address, actions in changes if not actions <= allowed)


def require_safe_plan(show_json: str, allow: str, origin: str) -> None:
    """The gate between `show -json` and `apply`: every action inside
    SAFE_ACTIONS, or `delete` with ALLOW_DESTROY=yes from the command line
    ($(origin ALLOW_DESTROY), the one `confirmed` predicate) — the toggle-flip
    teardown says so explicitly; an apply that merely forgot a toggle does not.
    Anything else refuses, the addresses named."""
    changes = planned_changes(show_json)
    unknown = unsafe_changes(changes, SAFE_ACTIONS | {DELETE})
    if unknown:
        die(
            "tf-apply: refused — the plan has actions outside "
            f"{{{', '.join(sorted(SAFE_ACTIONS | {DELETE}))}}} for "
            + ", ".join(unknown)
            + "; nothing applied (a state drop or an unknown verb never applies here)"
        )
    deletes = unsafe_changes(changes, SAFE_ACTIONS)
    if deletes and not confirmed(allow, origin):
        die(
            "tf-apply: refused — the plan destroys "
            + ", ".join(deletes)
            + "; if that is intended (the toggle-flip teardown), re-run with "
            "ALLOW_DESTROY=yes on the command line; if not, the VARS you passed "
            "omit a toggle that is currently applied"
        )


def _run(runner: Runner, argv: list[str], label: str) -> int | None:
    """Run one terraform argv through the injected runner; returns the exit code,
    or None when the binary is missing (a clean FAIL, already reported — None is a
    distinct sentinel so a real terraform exit 127 still prints its FAIL line)."""
    try:
        return runner(argv, env=tf_env()).returncode
    except FileNotFoundError:
        print(f"{label} FAIL: terraform not on PATH")
        return None


def _apply(
    runner: Runner, var: list[str], project: str, allow: str, allow_origin: str
) -> int:
    """plan -out → show -json → require_safe_plan → apply <planfile>. The plan
    file holds variable values, so it lives in TMPDIR and is removed on every
    path."""
    fd, plan_path = tempfile.mkstemp(prefix="tfplan-", suffix=".bin")
    os.close(fd)
    try:
        rc = _run(
            runner,
            _TF + ["plan", "-input=false", f"-out={plan_path}", *var],
            "tf-apply",
        )
        if rc is None:
            return 1
        if rc != 0:
            print(f"tf-apply FAIL: {project} (plan)")
            return 1
        try:
            shown = runner(
                _TF + ["show", "-json", plan_path],
                env=tf_env(),
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            print("tf-apply FAIL: terraform not on PATH")
            return 1
        if shown.returncode != 0:
            print(f"tf-apply FAIL: {project} (show)")
            return 1
        require_safe_plan(shown.stdout, allow, allow_origin)
        rc = _run(runner, _TF + ["apply", "-input=false", plan_path], "tf-apply")
        if rc is None:
            return 1
        if rc != 0:
            print(f"tf-apply FAIL: {project}")
            return 1
        print(f"tf-apply OK: {project}")
        return 0
    finally:
        if os.path.exists(plan_path):
            os.unlink(plan_path)


def tf(
    cmd: str,
    project: str = "",
    confirm: str = "",
    origin: str = "",
    runner: Runner = subprocess.run,
    vars_: str = "",
    vars_origin: str = "",
    allow_destroy: str = "",
    allow_destroy_origin: str = "",
) -> int:
    if cmd in CLOUD_MUTATING:
        require_confirm(cmd, confirm, origin)
    refuse_env_tf_vars(cmd)  # every command, validate too (it evaluates validations)
    if cmd == "validate":
        for step in (
            _TF + ["init", "-backend=false", "-input=false", "-lockfile=readonly"],
            _TF + ["validate"],
            _TF + ["fmt", "-check", "-recursive"],
        ):
            rc = _run(runner, step, "tf-validate")
            if rc is None:
                return 1  # missing binary — already reported
            if rc != 0:
                print(f"tf-validate FAIL: {' '.join(step[2:])}")
                return 1
        print("tf-validate OK")
        return 0
    validate_project(project)
    refuse_auto_tfvars(cmd)
    refuse_cloud_env(f"tf-{cmd}")
    var = ["-var", f"project_id={project}", *parse_vars(vars_, vars_origin)]
    if cmd == "apply":
        return _apply(runner, var, project, allow_destroy, allow_destroy_origin)
    argv = {
        "plan": _TF + ["plan", "-input=false", *var],
        "destroy": _TF + ["destroy", "-input=false", "-auto-approve", *var],
    }[cmd]
    rc = _run(runner, argv, f"tf-{cmd}")
    if rc is None:
        return 1  # missing binary — already reported
    if rc != 0:
        print(f"tf-{cmd} FAIL: {project}")
        return 1
    print(f"tf-{cmd} OK: {project}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    for name in ("plan", "apply", "destroy", "freeze"):
        p = sub.add_parser(name)
        if name != "freeze":
            p.add_argument("--project", default="")
            p.add_argument("--vars", default="")
            p.add_argument("--vars-origin", default="")
        if name in CONFIRM_GATED:
            p.add_argument("--confirm", default="")
            p.add_argument("--confirm-origin", default="")
        if name == "apply":
            p.add_argument("--allow-destroy", default="")
            p.add_argument("--allow-destroy-origin", default="")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        return tf("validate")
    if a.cmd == "freeze":
        return freeze(a.confirm, a.confirm_origin)
    if a.cmd == "plan":
        return tf("plan", a.project, vars_=a.vars, vars_origin=a.vars_origin)
    if a.cmd == "apply":
        return tf(
            "apply",
            a.project,
            a.confirm,
            a.confirm_origin,
            vars_=a.vars,
            vars_origin=a.vars_origin,
            allow_destroy=a.allow_destroy,
            allow_destroy_origin=a.allow_destroy_origin,
        )
    return tf(
        "destroy",
        a.project,
        a.confirm,
        a.confirm_origin,
        vars_=a.vars,
        vars_origin=a.vars_origin,
    )


if __name__ == "__main__":
    sys.exit(main())
