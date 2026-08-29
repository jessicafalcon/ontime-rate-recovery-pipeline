output "raw_dataset" {
  description = "The raw landing dataset id (Phase 9b bq load target)."
  value       = module.bigquery.raw_dataset_id
}

output "models_dataset" {
  description = "The dbt models dataset id (profiles.yml bigquery target)."
  value       = module.bigquery.models_dataset_id
}

output "staging_bucket" {
  description = "The GCS artifacts/staging bucket (NOT the Terraform state bucket)."
  value       = module.gcs.bucket_name
}

output "pipeline_service_account" {
  description = "The least-privilege pipeline service account email."
  value       = module.iam.service_account_email
}
