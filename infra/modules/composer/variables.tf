variable "project_id" {
  type = string
}

variable "region" {
  type = string

  # The same shape check as the root's, co-located with the interpolation sites
  # (the environment region/config) — round 2 #6; pinned equal to the root's by
  # tests/test_infra.py.
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]{1,2}$", var.region))
    error_message = "region must be a GCP region id like us-central1."
  }
}

variable "sa_email" {
  description = "The pipeline service account (module.iam) — the environment's runtime identity (node_config.service_account) and the member of the one roles/composer.worker grant. Derived, not caller input (the Spanner sa_email pattern)."
  type        = string
}
