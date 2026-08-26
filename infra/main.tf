# GCP foundation (Phase 9a). One module per concern; bigquery/gcs/iam/budget are
# free/near-free and unconditional (ARCHITECTURE §6), composer/spanner are
# count-gated behind enable_* toggles that default false, so a default plan
# creates zero of them (spec invariant 2). Auth is ADC (local `gcloud`) or WIF
# (CI) — no service-account key, ever.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # The Terraform state backend is bootstrap-documented (docs/DEPLOYMENT.md), not
  # applied from a fresh clone: a bucket cannot create the backend that stores
  # its own state. Default backend is local, so `tf-plan` needs no setup. After
  # the one-time bootstrap, uncomment and `terraform init -migrate-state`:
  # backend "gcs" {
  #   bucket = "<project_id>-tfstate"
  #   prefix = "terraform/state"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
  # No `credentials`/keyfile: ADC or WIF only (spec invariant 3).
}

locals {
  state_bucket = var.state_bucket != "" ? var.state_bucket : "${var.project_id}-tfstate"
}

# The project's billing account and number — derived, so `project_id` stays the
# only required var (the budget needs neither as a separate input).
data "google_project" "this" {
  project_id = var.project_id
}

module "bigquery" {
  source         = "./modules/bigquery"
  project_id     = var.project_id
  region         = var.region
  raw_dataset    = var.raw_dataset
  models_dataset = var.models_dataset
}

module "gcs" {
  source     = "./modules/gcs"
  project_id = var.project_id
  region     = var.region
  bucket     = local.state_bucket
}

module "iam" {
  source            = "./modules/iam"
  project_id        = var.project_id
  raw_dataset       = module.bigquery.raw_dataset_id
  models_dataset    = module.bigquery.models_dataset_id
  bucket            = module.gcs.bucket_name
  github_repository = var.github_repository
}

module "budget" {
  source           = "./modules/budget"
  billing_account  = data.google_project.this.billing_account
  project_number   = data.google_project.this.number
  alert_thresholds = var.budget_alert_thresholds_usd
  display_name     = "ontime-${var.project_id}"
}

# Written, not applied: the toggle lands here (false); the body is Phase 11.
module "composer" {
  source     = "./modules/composer"
  count      = var.enable_composer ? 1 : 0
  project_id = var.project_id
  region     = var.region
}

# Written, not applied: the toggle lands here (false); the body is Phase 10.
module "spanner" {
  source     = "./modules/spanner"
  count      = var.enable_spanner ? 1 : 0
  project_id = var.project_id
  region     = var.region
}
