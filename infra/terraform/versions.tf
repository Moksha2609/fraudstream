terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Recommended: keep state in S3 with locking. Create the bucket/table first,
  # then uncomment and run `terraform init -migrate-state`.
  # backend "s3" {
  #   bucket         = "my-tf-state"
  #   key            = "fraudstream/terraform.tfstate"
  #   region         = "eu-west-1"
  #   dynamodb_table = "my-tf-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "fraudstream"
      ManagedBy = "terraform"
    }
  }
}
