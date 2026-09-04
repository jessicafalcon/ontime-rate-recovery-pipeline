output "environment_name" {
  description = "The Cloud Composer environment name (for the DEPLOYMENT runbook / a DAG trigger)."
  value       = google_composer_environment.this.name
}

output "dag_gcs_prefix" {
  description = "The gs:// prefix of the environment's DAG bucket the DAG is uploaded into."
  value       = google_composer_environment.this.config[0].dag_gcs_prefix
}

output "serving_image" {
  description = "The Artifact Registry image URI the KubernetesPodOperator pods pull (build + push with `make build-serving-image`)."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.serving.repository_id}/serving:latest"
}
