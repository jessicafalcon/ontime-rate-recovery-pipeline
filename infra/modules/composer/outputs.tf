output "environment_name" {
  description = "The Cloud Composer environment name (for the DEPLOYMENT runbook / a Phase 12 DAG trigger)."
  value       = google_composer_environment.this.name
}

output "dag_gcs_prefix" {
  description = "The gs:// prefix of the environment's DAG bucket the DAG is uploaded into."
  value       = google_composer_environment.this.config[0].dag_gcs_prefix
}
