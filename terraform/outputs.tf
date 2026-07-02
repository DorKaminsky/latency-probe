output "ecr_repository_url" {
  description = "Push images here: docker push <ecr_repository_url>:<tag>"
  value       = aws_ecr_repository.latency_probe.repository_url
}

output "eks_cluster_endpoint" {
  description = "kubectl uses this endpoint; update kubeconfig with: aws eks update-kubeconfig --name <cluster_name>"
  value       = aws_eks_cluster.main.endpoint
}

output "eks_cluster_name" {
  value = aws_eks_cluster.main.name
}
