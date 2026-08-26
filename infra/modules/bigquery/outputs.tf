output "raw_dataset_id" {
  value = google_bigquery_dataset.raw.dataset_id
}

output "models_dataset_id" {
  value = google_bigquery_dataset.models.dataset_id
}
