# n8n Usage Analysis

This project investigates **how n8n is actually used in real-world software projects**, based on publicly available GitHub repositories.

Instead of relying on job postings or marketing material, the analysis focuses on **repositories that deploy n8n in practice** (e.g. via Docker or Kubernetes). These repositories are automatically discovered using the GitHub API and filtered to ensure they represent genuine usage rather than plugins, templates, or documentation.

For each validated usage repository, the project:

* extracts high-level project context from the README,
* classifies the type of software being built using a structured, README-only taxonomy,
* and aggregates the results to identify common application domains, target users, and use-case patterns.

The goal is to provide an **evidence-based view of where and for what types of software n8n is used today**, with a particular focus on automation, integration, and data-related workloads.

The pipeline is fully reproducible and designed to prioritize precision, transparency, and explainability over raw volume.


