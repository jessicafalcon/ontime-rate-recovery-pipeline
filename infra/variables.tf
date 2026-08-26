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
  description = "Provision the Cloud Composer module (Phase 11). Off by default — nothing billable is left up."
  type        = bool
  default     = false
}

variable "enable_spanner" {
  description = "Provision the Spanner module (Phase 10). Off by default — the 90-day trial clock only starts on apply."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "owner/repo the WIF provider trusts for CI (short-lived OIDC, no key at rest)."
  type        = string
  default     = "jessicafalcon/ontime-rate-recovery-pipeline"
}

variable "budget_alert_thresholds_usd" {
  description = "Budget alert thresholds in USD (notify only — a budget does not stop spend)."
  type        = list(number)
  default     = [50, 150]
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

variable "state_bucket" {
  description = "GCS bucket for Terraform state + artifacts. Empty derives <project_id>-tfstate."
  type        = string
  default     = ""
}
