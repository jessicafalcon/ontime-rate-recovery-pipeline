variable "project_id" {
  type = string
}

variable "region" {
  type = string

  # The same shape check as the root's, co-located with the interpolation
  # site(s) — round 2 #6; pinned equal to the root's by tests/test_infra.py.
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]{1,2}$", var.region))
    error_message = "region must be a GCP region id like us-central1."
  }
}

variable "raw_dataset" {
  type = string
}

variable "models_dataset" {
  type = string
}
