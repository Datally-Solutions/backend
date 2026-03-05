output "ingest_function_url" {
  description = "URL to use in ESP32 secrets.h"
  value       = google_cloudfunctions2_function.ingest.service_config[0].uri
}

output "function_sa_email" {
  description = "Cloud Function service account email"
  value       = google_service_account.function_sa.email
}