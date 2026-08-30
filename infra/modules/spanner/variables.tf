variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "project_number" {
  description = "The project number — the BigQuery Connection service agent's email is derived from it (root data.google_project)."
  type        = string
}

variable "raw_dataset" {
  description = "The raw dataset the federation view lands in (module.bigquery.raw_dataset_id — the view depends on the dataset)."
  type        = string
}

variable "sa_email" {
  description = "The pipeline service account (module.iam) — databaseUser on the one database, connectionUser on the one connection."
  type        = string
}
