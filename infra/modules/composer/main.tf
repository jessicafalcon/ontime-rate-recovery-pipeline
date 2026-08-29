# Written, not applied. The `enable_composer` toggle (default false) count-gates
# this module from main.tf, so a default plan creates nothing here. The Cloud
# Composer environment + DAG upload land in Phase 11 (docs/PHASES.md); until then
# the module is an empty shell that only declares the inputs it will take, so the
# toggle can exist today without any billable resource.
