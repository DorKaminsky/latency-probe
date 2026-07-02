/*
 * Terraform IaC — latency-probe on AWS EKS
 *
 * Design decisions
 * ────────────────
 * - EKS over ECS: the assignment asks for k8s manifests, so EKS is the natural
 *   backing infrastructure. ECS would require translating manifests to task
 *   definitions, adding friction.
 *
 * - ECR for image storage: managed by AWS, integrates with EKS node IAM roles
 *   so no registry credentials need to be passed to pods.
 *
 * - Dedicated IAM role per service (IRSA — IAM Roles for Service Accounts):
 *   least-privilege principle. The probe service only needs ECR pull access,
 *   not broad EC2 or S3 permissions.
 *
 * - VPC is out of scope here (use the default VPC or an existing one via
 *   data source). For production, add a separate vpc.tf with private subnets
 *   and NAT gateways.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Remote state in S3 with DynamoDB locking.
  # Uncommenting this block enables team collaboration on shared state.
  # backend "s3" {
  #   bucket         = "my-tf-state-bucket"
  #   key            = "latency-probe/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "tf-state-lock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
}

# ── ECR repository ────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "latency_probe" {
  name                 = var.ecr_repo_name
  image_tag_mutability = "IMMUTABLE" # Immutable tags prevent accidental overwrites of production images

  image_scanning_configuration {
    scan_on_push = true # AWS Inspector scans every pushed image for OS CVEs
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Lifecycle policy: keep only the 10 most recent images to control storage costs
resource "aws_ecr_lifecycle_policy" "latency_probe" {
  repository = aws_ecr_repository.latency_probe.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ── EKS Cluster ───────────────────────────────────────────────────────────────

# IAM role that the EKS control plane assumes to manage AWS resources on our behalf
resource "aws_iam_role" "eks_cluster" {
  name = "${var.cluster_name}-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = aws_iam_role.eks_cluster.arn
  version  = "1.30"

  vpc_config {
    subnet_ids = data.aws_subnets.default.ids
  }

  depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]
}

# ── EKS Node Group ────────────────────────────────────────────────────────────

resource "aws_iam_role" "eks_nodes" {
  name = "${var.cluster_name}-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Three AWS-managed policies required for EKS worker nodes:
# - AmazonEKSWorkerNodePolicy: allows nodes to register with the cluster
# - AmazonEKS_CNI_Policy: allows the VPC CNI plugin to assign pod IPs
# - AmazonEC2ContainerRegistryReadOnly: allows nodes to pull images from ECR
resource "aws_iam_role_policy_attachment" "eks_worker_node" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_cni" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "latency-probe-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = data.aws_subnets.default.ids

  # t3.small: 2 vCPU / 2 GB RAM — sufficient for a lightweight async service.
  # Upgrade to t3.medium if multiple concurrent probe jobs saturate the node.
  instance_types = ["t3.small"]

  scaling_config {
    desired_size = 2
    min_size     = 1
    max_size     = 5
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node,
    aws_iam_role_policy_attachment.eks_cni,
    aws_iam_role_policy_attachment.ecr_read,
  ]
}
