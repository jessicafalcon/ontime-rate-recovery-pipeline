# Root inputs. `project_id` is the ONLY variable without a default — a fresh
# clone plans with just that one value (spec Done-when 1, invariant 1).

variable "project_id" {
  description = "The GCP project. The only required variable."
  type        = string

  # The same shape infra/cli.py enforces (PROJECT_RE), so a tfvars / direct
  # `terraform apply` cannot bypass the make-target validation (round 3 #13).
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a GCP project id: [a-z][a-z0-9-]{4,28}[a-z0-9]."
  }
}

variable "region" {
  description = "Region for the datasets, bucket, and (toggled) Composer/Spanner."
  type        = string
  default     = "us-central1"
}

variable "enable_composer" {
  description = "Provision the Cloud Composer module (Phase 11). Off — nothing billable is left up."
  type        = bool
  default     = false
}

variable "enable_spanner" {
  description = "Provision the Spanner module (Phase 10). Off — the 90-day trial clock only starts on apply."
  type        = bool
  default     = false
}

variable "enable_ci_wif" {
  description = "Create the GitHub WIF pool/provider + SA impersonation binding for CI. Off — a default apply builds no cross-repo trust (Amendment H)."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "owner/repo the WIF provider trusts for CI (short-lived OIDC, no key at rest). Only used when enable_ci_wif is true — a fork sets its own."
  type        = string
  default     = "jessicafalcon/ontime-rate-recovery-pipeline"

  # Interpolated into the provider's CEL attribute_condition and the principalSet
  # member — a shape check keeps a hostile TF_VAR from injecting CEL/`||`.
  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must be owner/repo."
  }
}

variable "github_ref" {
  description = "The git ref CI must run on to impersonate the SA (branch scoping — not any branch)."
  type        = string
  default     = "refs/heads/main"

  validation {
    condition     = can(regex("^refs/(heads|tags)/[A-Za-z0-9._/-]+$", var.github_ref))
    error_message = "github_ref must be a refs/heads/... or refs/tags/... path."
  }
}

variable "budget_alert_thresholds_usd" {
  description = "Budget alert thresholds in the billing account's currency (USD on a USD account; notify only — a budget does not stop spend)."
  type        = list(number)
  default     = [50, 150]

  # Non-empty AND every threshold strictly positive — the budget amount is the
  # smallest threshold, so a 0 would divide-by-zero the percents.
  validation {
    condition     = length(var.budget_alert_thresholds_usd) > 0 && alltrue([for t in var.budget_alert_thresholds_usd : t > 0])
    error_message = "budget_alert_thresholds_usd must be non-empty and all strictly positive."
  }
}

variable "raw_dataset" {
  description = "BigQuery dataset for the raw Amplitude-shape landing (Phase 9b bq load target)."
  type        = string
  default     = "raw"
}

variable "models_dataset" {
  description = "BigQuery dataset for the dbt models (profiles.yml bigquery target dataset)."
  type        = string
  default     = "ontime"
}

# NOTE: the managed staging bucket is NOT a variable — it is always
# `${project_id}-ontime`, so a caller can never point it at the bootstrap
# `${project_id}-tfstate` state bucket and have Terraform manage (and destroy)
# its own state (review round 2 #1). The state bucket stays bootstrap-only.
