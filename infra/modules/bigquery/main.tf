# Two datasets: `raw` (the Amplitude-shape landing 9b's `bq load` targets) and
# `ontime` (the dbt models — profiles.yml's bigquery target dataset). Empty
# datasets are free to leave up (ARCHITECTURE §6). Regional location = var.region.

resource "google_bigquery_dataset" "raw" {
  project     = var.project_id
  dataset_id  = var.raw_dataset
  location    = var.region
  description = "Raw Amplitude-shape landing (Phase 9b bq load target)."
}

resource "google_bigquery_dataset" "models" {
  project     = var.project_id
  dataset_id  = var.models_dataset
  location    = var.region
  description = "dbt models (staging -> scores); profiles.yml bigquery target dataset."
}
