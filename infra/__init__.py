"""Terraform foundation (Phase 9a). `infra/cli.py` validates and launches
terraform; the `.tf` tree is the config. Now that `cli.py` exists, `infra` is a
guarded pipeline directory (dropped from the isolation grep's EXEMPT set, Phase
8b instruction) — it never reads the generator side-file."""
