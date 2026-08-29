# Root inputs. `project_id` is the ONLY variable without a default — a fresh
# clone plans with just that one value (spec Done-when 1, invariant 1).

variable "project_id" {
  description = "The GCP project. The only required variable."
  type        = string
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

variable "github_repository" {
  description = "owner/repo the WIF provider trusts for CI (short-lived OIDC, no key at rest)."
  type        = string
  default     = "jessicafalcon/ontime-rate-recovery-pipeline"
}

variable "github_ref" {
  description = "The git ref CI must run on to impersonate the SA (branch scoping — not any branch)."
  type        = string
  default     = "refs/heads/main"
}

variable "budget_alert_thresholds_usd" {
  description = "Budget alert thresholds in USD (notify only — a budget does not stop spend)."
  type        = list(number)
  default     = [50, 150]

  validation {
    condition     = length(var.budget_alert_thresholds_usd) > 0
    error_message = "budget_alert_thresholds_usd must list at least one threshold."
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

variable "staging_bucket" {
  description = "GCS bucket for pipeline artifacts / the 9b BigQuery landing. Empty derives <project_id>-ontime. NOT the Terraform state bucket (that is bootstrap-only — docs/DEPLOYMENT.md)."
  type        = string
  default     = ""
}
