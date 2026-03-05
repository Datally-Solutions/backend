terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "YOUR_PROJECT_ID-tfstate"
    prefix = "backend"
  }
}

provider "google" {
  project = var.GCP_PROJECT_ID
  region  = var.GCP_REGION
}

# -------------------------------------------------------
# Service Account for Cloud Function
# -------------------------------------------------------
resource "google_service_account" "function_sa" {
  account_id   = "litter-function-sa"
  display_name = "Cat Litter Function SA"
}

# -------------------------------------------------------
# Custom Role for Cloud Function
# -------------------------------------------------------
resource "google_project_iam_custom_role" "function_role" {
  role_id     = "litterFunctionRole"
  title       = "Cat Litter Function Role"
  description = "Minimal permissions for the litter ingest Cloud Function"
  stage       = "GA"

  permissions = [
    # BigQuery — insert rows only
    "bigquery.datasets.get",
    "bigquery.tables.get",
    "bigquery.tables.updateData",
    "bigquery.jobs.create",

    # Secret Manager — read secrets only
    "secretmanager.versions.access",

    # Logging — write logs
    "logging.logEntries.create",

    # Monitoring — write metrics
    "monitoring.timeSeries.create",
  ]
}

resource "google_project_iam_member" "function_custom_role" {
  project = var.GCP_PROJECT_ID
  role    = google_project_iam_custom_role.function_role.id
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}

# -------------------------------------------------------
# GCS bucket for function source
# -------------------------------------------------------
resource "google_storage_bucket" "functions_source" {
  name                        = "${var.GCP_PROJECT_ID}-functions-source"
  location                    = "EU"
  force_destroy               = true
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }
}

# -------------------------------------------------------
# Zip and upload function source
# -------------------------------------------------------
data "archive_file" "ingest_source" {
  type        = "zip"
  source_dir  = "${path.module}/functions/ingest"
  output_path = "${path.module}/tmp/ingest.zip"
}

resource "google_storage_bucket_object" "ingest_source" {
  name   = "ingest/ingest-${data.archive_file.ingest_source.output_md5}.zip"
  bucket = google_storage_bucket.functions_source.name
  source = data.archive_file.ingest_source.output_path
}

# -------------------------------------------------------
# Cloud Function
# -------------------------------------------------------
resource "google_cloudfunctions2_function" "ingest" {
  name     = "litter-ingest"
  location = var.GCP_REGION

  build_config {
    runtime     = "python311"
    entry_point = "ingest_litter_event"

    source {
      storage_source {
        bucket = google_storage_bucket.functions_source.name
        object = google_storage_bucket_object.ingest_source.name
      }
    }
  }

  service_config {
    max_instance_count    = 10
    min_instance_count    = 0
    available_memory      = "256M"
    timeout_seconds       = 30
    service_account_email = google_service_account.function_sa.email

    environment_variables = {
      PROJECT_ID       = var.GCP_PROJECT_ID
      BIGQUERY_DATASET = "litiere"
      BIGQUERY_TABLE   = "events"
    }

    secret_environment_variables {
      key        = "INGEST_TOKEN"
      project_id = var.GCP_PROJECT_ID
      secret     = var.ingest_token_secret_id
      version    = "latest"
    }
  }
}

# Allow unauthenticated calls
#resource "google_cloud_run_service_iam_member" "ingest_public" {
 # location = var.GCP_REGION
  #service  = google_cloudfunctions2_function.ingest.name
  #role     = "roles/run.invoker"
  #member   = "allUsers"
#}