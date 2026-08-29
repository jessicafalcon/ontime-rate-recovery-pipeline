output "service_account_email" {
  value = google_service_account.pipeline.email
}

output "workload_identity_provider" {
  # null when enable_ci_wif is false (no provider exists).
  value = var.enable_ci_wif ? google_iam_workload_identity_pool_provider.github[0].name : null
}
