terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "cat-litter-monitor-tfstate"
    prefix = "backend"
  }
}

provider "google" {
  project = var.GCP_PROJECT_ID
  region  = var.GCP_REGION
}

data "google_project" "project" {
  project_id = var.GCP_PROJECT_ID
}


# -------------------------------------------------------
# Service Account for Cloud Functions
# -------------------------------------------------------
resource "google_service_account" "function_sa" {
  account_id   = "litter-function-sa"
  display_name = "Cat Litter Function SA"
}

# -------------------------------------------------------
# Custom Role for Cloud Functions
# -------------------------------------------------------
resource "google_project_iam_custom_role" "function_role" {
  role_id     = "litterFunctionRole"
  title       = "Cat Litter Function Role"
  description = "Minimal permissions for the litter Cloud Functions"
  stage       = "GA"

  permissions = [
    # BigQuery — insert + query
    "bigquery.datasets.get",
    "bigquery.tables.get",
    "bigquery.tables.updateData",
    "bigquery.jobs.create",
    "bigquery.tables.getData",
    "bigquery.jobs.get",   

    # Firestore
    "datastore.entities.create",
    "datastore.entities.update",
    "datastore.entities.get",
    "datastore.entities.list",

    # Secret Manager — read secrets only
    "secretmanager.versions.access",

    # Logging
    "logging.logEntries.create",

    # Monitoring
    "monitoring.timeSeries.create",

    # FCM (Firebase Messaging) via Cloud Messaging API
    "cloudmessaging.messages.create",
  ]
}

resource "google_project_iam_member" "function_custom_role" {
  project = var.GCP_PROJECT_ID
  role    = google_project_iam_custom_role.function_role.id
  member  = "serviceAccount:${google_service_account.function_sa.email}"
}

# -------------------------------------------------------
# GCS bucket for function sources
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
# INGEST FUNCTION
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
    max_instance_count               = 1
    min_instance_count               = 0
    max_instance_request_concurrency = 1
    ingress_settings                 = "ALLOW_ALL"
    available_memory                 = "256M"
    timeout_seconds                  = 30
    service_account_email            = google_service_account.function_sa.email

    environment_variables = {
      PROJECT_ID          = var.GCP_PROJECT_ID
      BIGQUERY_DATASET    = "litiere"
      BIGQUERY_TABLE      = "events"
      FIRESTORE_DATABASE  = var.firestore_database
    }

    secret_environment_variables {
      key        = "INGEST_TOKEN"
      project_id = var.GCP_PROJECT_ID
      secret     = var.ingest_token_secret_id
      version    = "latest"
    }
  }
}

# Allow unauthenticated calls (security via X-Ingest-Token)
resource "google_cloud_run_service_iam_member" "ingest_public" {
  location = var.GCP_REGION
  service  = google_cloudfunctions2_function.ingest.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -------------------------------------------------------
# HEALTH CHECKER FUNCTION
# -------------------------------------------------------
data "archive_file" "health_checker_source" {
  type        = "zip"
  source_dir  = "${path.module}/functions/health_checker"
  output_path = "${path.module}/tmp/health_checker.zip"
}

resource "google_storage_bucket_object" "health_checker_source" {
  name   = "health_checker/health_checker-${data.archive_file.health_checker_source.output_md5}.zip"
  bucket = google_storage_bucket.functions_source.name
  source = data.archive_file.health_checker_source.output_path
}

resource "google_cloudfunctions2_function" "health_checker" {
  name     = "litter-health-checker"
  location = var.GCP_REGION

  build_config {
    runtime     = "python311"
    entry_point = "health_checker"

    source {
      storage_source {
        bucket = google_storage_bucket.functions_source.name
        object = google_storage_bucket_object.health_checker_source.name
      }
    }
  }

  service_config {
    max_instance_count    = 1
    min_instance_count    = 0
    available_memory      = "256M"
    timeout_seconds       = 120
    service_account_email = google_service_account.function_sa.email

    environment_variables = {
      PROJECT_ID         = var.GCP_PROJECT_ID
      BIGQUERY_DATASET   = "litiere"
      BIGQUERY_TABLE     = "events"
      FIRESTORE_DATABASE = var.firestore_database
    }
  }
}

# -------------------------------------------------------
# CLOUD SCHEDULER — health check every day at 9am Paris
# -------------------------------------------------------
resource "google_service_account" "scheduler_sa" {
  account_id   = "health-checker-scheduler"
  display_name = "Health Checker Scheduler SA"
}

resource "google_cloud_run_service_iam_member" "scheduler_invoker" {
  location = var.GCP_REGION
  service  = google_cloudfunctions2_function.health_checker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

resource "google_cloud_scheduler_job" "health_check_daily" {
  name      = "health-check-daily"
  region    = "europe-west1"
  schedule  = "0 9 * * *"
  time_zone = "Europe/Paris"

  http_target {
    uri         = google_cloudfunctions2_function.health_checker.service_config[0].uri
    http_method = "POST"
    body        = base64encode("{}")

    oidc_token {
      service_account_email = google_service_account.scheduler_sa.email
      audience              = google_cloudfunctions2_function.health_checker.service_config[0].uri
    }
  }

  retry_config {
    retry_count = 3
  }
}

resource "google_cloud_run_v2_service" "litter_api" {
  name     = "litter-api"
  location = var.GCP_REGION

  template {
    service_account = google_service_account.function_sa.email

    containers {
      image = "${var.GCP_REGION}-docker.pkg.dev/${var.GCP_PROJECT_ID}/${var.GCP_PROJECT_ID}-registry-docker/litter-api:latest"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "PROJECT_ID"
        value = var.GCP_PROJECT_ID
      }
      env {
        name  = "BIGQUERY_DATASET"
        value = "litiere"
      }
      env {
        name  = "BIGQUERY_TABLE"
        value = "events"
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = var.firestore_database
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
  }
}

# Allow unauthenticated calls — auth handled by Firebase token validation in the app
resource "google_cloud_run_v2_service_iam_member" "litter_api_public" {
  project  = var.GCP_PROJECT_ID
  location = var.GCP_REGION
  name     = google_cloud_run_v2_service.litter_api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_service_account_iam_member" "cloudbuild_sa_user" {
  service_account_id = google_service_account.function_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
}
