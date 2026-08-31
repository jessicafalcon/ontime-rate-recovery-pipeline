variable "project_id" {
  type = string
}

variable "region" {
  type = string

  # The same shape check as the root's, declared where the module will use it
  # (Phase 11: the environment's location; today only this validation reads
  # it) — round 2 #6; pinned equal to the root's by tests/test_infra.py.
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]{1,2}$", var.region))
    error_message = "region must be a GCP region id like us-central1."
  }
}
