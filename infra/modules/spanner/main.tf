# Written, not applied. The `enable_spanner` toggle (default false) count-gates
# this module from main.tf, so a default plan creates nothing here — the 90-day
# trial clock only starts on a Phase 10 apply (BACKLOG "Spanner 90-day trial
# expiry"). The Spanner instance + `send_schedule` schema land in Phase 10; until
# then the module is an empty shell that only declares its inputs.
