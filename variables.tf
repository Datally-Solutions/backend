variable "GCP_PROJECT_ID" {
  description = "GCP Project ID"
  type        = string
}

variable "GCP_REGION" {
  description = "GCP region"
  type        = string
  default     = "europe-west9"
}

variable "ingest_token_secret_id" {
  description = "Secret Manager secret ID for ingest token"
  type        = string
  default     = "litter-ingest-token"
}
