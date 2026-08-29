# One least-privilege service account. It gets exactly what the pipeline uses:
# run BigQuery jobs (project-level jobUser), edit the two datasets
# (dataset-scoped dataEditor), read/write the one bucket (objectAdmin) — never
# roles/owner or roles/editor (spec invariant 4). CI authenticates by Workload
# Identity Federation: a GitHub OIDC token is exchanged for a short-lived
# credential impersonating this SA, so no key is ever downloaded or committed.

resource "google_service_account" "pipeline" {
  project      = var.project_id
  account_id   = "ontime-pipeline"
  display_name = "On-Time pipeline (BigQuery + GCS, least privilege)"
}

resource "google_project_iam_member" "bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_bigquery_dataset_iam_member" "raw_data_editor" {
  project    = var.project_id
  dataset_id = var.raw_dataset
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_bigquery_dataset_iam_member" "models_data_editor" {
  project    = var.project_id
  dataset_id = var.models_dataset
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_storage_bucket_iam_member" "bucket_object_admin" {
  bucket = var.bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

# Workload Identity Federation — GitHub Actions OIDC, no key at rest.
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "ontime-github-pool"
  display_name              = "On-Time GitHub CI"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub Actions OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    # A combined repo@ref attribute so the impersonation binding scopes BOTH at
    # once — not repo-only with ref living solely in the provider condition
    # (review round 2 #3).
    "attribute.repo_ref" = "assertion.repository + \"@\" + assertion.ref"
  }
  # Only THIS repo AND only the trusted ref (default refs/heads/main) — not any
  # branch of the repo — can exchange a token (spec invariant 7).
  attribute_condition = "assertion.repository == \"${var.github_repository}\" && assertion.ref == \"${var.github_ref}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Only CI runs of THIS repo on the trusted ref may impersonate the SA. The
# binding is on the combined repo@ref attribute, so even a future second, looser
# provider on the same pool could not widen it to another branch.
resource "google_service_account_iam_member" "wif_impersonation" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repo_ref/${var.github_repository}@${var.github_ref}"
}
