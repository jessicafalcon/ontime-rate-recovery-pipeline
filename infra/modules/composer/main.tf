# Cloud Composer orchestration layer, count-gated from the root
# (`module "composer" { count = var.enable_composer ? 1 : 0 }`), so a default
# plan/apply creates NOTHING here. Cloud Composer is GCP's managed Apache Airflow.
#
# fix/composer-cosmos (ROADMAP item 7) turned this from Phase 11's plan-only
# "the DAG parses" into the runtime that EXECUTES on a worker:
#   - the environment installs astronomer-cosmos + the k8s provider via
#     software_config.pypi_packages (NEVER uv.lock) and carries the DAG's env
#     (project, serving image, dbt project);
#   - it uploads the Cosmos + KubernetesPodOperator DAG, the dbt project and its
#     precompiled manifest into the DAG bucket (the make-based Phase 8b DAG is no
#     longer uploaded — one `pipeline`-shaped DAG per bucket);
#   - an Artifact Registry repo holds the serving+landing image the KPO pods run,
#     with a repo-scoped `artifactregistry.reader` grant to the pipeline SA.
#
# Cost floor ~$300+/mo, billed continuously — so it is applied for ONE demo-day
# run (7b) and destroyed the same session (the toggle-flip `enable_composer=false`
# re-apply). `tf-apply` plans first and refuses a destroying plan without
# ALLOW_DESTROY=yes (Amendment N1). Nothing here is applied in 7a (plan-clean).

# The Composer API, kept on at destroy (a project-wide API another workload may
# use is never disabled by our teardown; enablement is free).
resource "google_project_service" "composer" {
  project            = var.project_id
  service            = "composer.googleapis.com"
  disable_on_destroy = false
}

# Artifact Registry API — the serving image's registry (kept on at destroy, free).
resource "google_project_service" "artifactregistry" {
  project            = var.project_id
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

# The environment's service account needs roles/composer.worker (the documented
# minimum). Granted to the EXISTING pipeline SA (module.iam), never the default
# Compute SA. Project-scoped because composer.worker is a project role.
resource "google_project_iam_member" "worker" {
  project = var.project_id
  role    = "roles/composer.worker"
  member  = "serviceAccount:${var.sa_email}"

  depends_on = [google_project_service.composer]
}

# The serving+landing image the KubernetesPodOperator pods run (bq_load,
# spanner_load, writeback). One Docker repo; the image is built + pushed by
# `make build-serving-image` (7b). repository_id "ontime" — pinned equal to
# pipeline.cli.SERVING_IMAGE_REPO by tests/test_infra.py.
resource "google_artifact_registry_repository" "serving" {
  project       = var.project_id
  location      = var.region
  repository_id = "ontime"
  format        = "DOCKER"
  description   = "serving+landing image for the Composer KubernetesPodOperator steps"

  depends_on = [google_project_service.artifactregistry]
}

# The pipeline SA pulls the image at pod start — a REPO-scoped reader grant (least
# privilege, not project-level), so test_composer_runtime_grant_scope still sees
# exactly one project-level grant (composer.worker).
resource "google_artifact_registry_repository_iam_member" "puller" {
  project    = var.project_id
  location   = google_artifact_registry_repository.serving.location
  repository = google_artifact_registry_repository.serving.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${var.sa_email}"
}

# The smallest environment (ENVIRONMENT_SIZE_SMALL) on the Composer-3/Airflow-2
# image, running as the pipeline SA. software_config installs cosmos + the k8s
# provider (Composer-only, never uv.lock — the offline suite stubs them) and sets
# the DAG's env: the target project, the serving image URI, and OTR_GCP_PROJECT
# for the dbt profile Cosmos runs in a per-run virtualenv.
resource "google_composer_environment" "this" {
  name    = "ontime"
  project = var.project_id
  region  = var.region

  config {
    environment_size = "ENVIRONMENT_SIZE_SMALL"

    software_config {
      image_version = "composer-3-airflow-2"

      pypi_packages = {
        "astronomer-cosmos"                        = ">=1.8,<2"
        "apache-airflow-providers-cncf-kubernetes" = ">=8,<9"
      }

      env_variables = {
        OTR_DAG_PROJECT   = var.project_id
        OTR_GCP_PROJECT   = var.project_id
        OTR_SERVING_IMAGE = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.serving.repository_id}/serving:latest"
        DO_NOT_TRACK      = "1"
      }
    }

    node_config {
      service_account = var.sa_email
    }
  }

  depends_on = [google_project_iam_member.worker]
}

locals {
  # The environment's DAG bucket; `dag_gcs_prefix` is `gs://<bucket>/dags`.
  # Split to the bucket name: ["gs:", "", "<bucket>", "dags"][2].
  dag_bucket = split("/", google_composer_environment.this.config[0].dag_gcs_prefix)[2]

  # The dbt project SOURCE files (models, macros, tests, profiles.yml, project
  # yml) — target/ and logs/ excluded (target/manifest.json is uploaded on its
  # own below). `source` points at repo files, never an inline heredoc.
  dbt_src_files = toset([
    for f in fileset("${path.module}/../../../dbt", "**") : f
    if !startswith(f, "target/") && !startswith(f, "logs/")
  ])
}

# The Cosmos + KubernetesPodOperator DAG and its two stdlib helpers, flat in the
# DAG bucket (only `dags/` is on the worker's sys.path — the dual-path imports
# resolve `import composer_tasks` / `import failure_email` there).
resource "google_storage_bucket_object" "composer_dag" {
  name   = "dags/composer_dag.py"
  bucket = local.dag_bucket
  source = "${path.module}/../../../orchestration/dags/composer_dag.py"
}

resource "google_storage_bucket_object" "composer_tasks" {
  name   = "dags/composer_tasks.py"
  bucket = local.dag_bucket
  source = "${path.module}/../../../orchestration/composer_tasks.py"
}

resource "google_storage_bucket_object" "failure_email" {
  name   = "dags/failure_email.py"
  bucket = local.dag_bucket
  source = "${path.module}/../../../orchestration/failure_email.py"
}

# The dbt project under dags/dbt/ (Composer mounts the bucket at
# /home/airflow/gcs/dags), so Cosmos reads /home/airflow/gcs/dags/dbt.
resource "google_storage_bucket_object" "dbt_project" {
  for_each = local.dbt_src_files
  name     = "dags/dbt/${each.value}"
  bucket   = local.dag_bucket
  source   = "${path.module}/../../../dbt/${each.value}"
}

# The precompiled manifest Cosmos loads (LoadMode.DBT_MANIFEST) so the scheduler
# runs no dbt at parse. A gitignored build artifact — the runbook runs
# `make composer-dbt-manifest` before `tf-plan`/apply (the source must exist).
resource "google_storage_bucket_object" "dbt_manifest" {
  name   = "dags/dbt/target/manifest.json"
  bucket = local.dag_bucket
  source = "${path.module}/../../../dbt/target/manifest.json"
}
