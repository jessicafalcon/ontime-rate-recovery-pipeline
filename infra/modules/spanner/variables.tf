variable "project_id" {
  type = string
}

variable "region" {
  type = string

  # The same shape check as the root's, co-located with the interpolation
  # sites (the view's SQL literal, the instance config, the connection
  # location) — round 2 #6; pinned equal to the root's by tests/test_infra.py.
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]{1,2}$", var.region))
    error_message = "region must be a GCP region id like us-central1."
  }
}

variable "raw_dataset" {
  description = "The raw dataset the federation view lands in (module.bigquery.raw_dataset_id — the view depends on the dataset)."
  type        = string
}

variable "sa_email" {
  description = "The pipeline service account (module.iam) — databaseUser on the one database, connectionUser on the one connection."
  type        = string
}
