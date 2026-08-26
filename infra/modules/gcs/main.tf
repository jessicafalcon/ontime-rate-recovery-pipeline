# One bucket: Terraform state (after the documented bootstrap) + artifacts.
# Versioned (state history), uniform access (no per-object ACLs), force_destroy
# so `tf-destroy` removes it even with objects — nothing billable survives.

resource "google_storage_bucket" "this" {
  project                     = var.project_id
  name                        = var.bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  versioning {
    enabled = true
  }
}
