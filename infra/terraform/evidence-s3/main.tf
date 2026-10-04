terraform {
  required_version = ">= 1.5, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-2"
}

resource "aws_s3_bucket" "evidence" {
  bucket_prefix = "boram3-cctv-evidence-"
  force_destroy = false

  tags = {
    Project   = "boram3-cctv"
    Purpose   = "candidate-evidence"
    ManagedBy = "Terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket = aws_s3_bucket.evidence.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

output "runpod_environment" {
  description = "Non-secret settings for the existing Runpod uploader."
  value = {
    EVIDENCE_STORAGE   = "s3"
    S3_BUCKET          = aws_s3_bucket.evidence.id
    AWS_DEFAULT_REGION = "ap-northeast-2"
    S3_PREFIX          = "cctv-evidence"
  }
}

output "runpod_upload_policy_json" {
  description = "Attach to the chosen upload identity; this configuration does not create or alter IAM identities."
  value = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject"]
      Resource = "${aws_s3_bucket.evidence.arn}/cctv-evidence/*"
    }]
  })
}
