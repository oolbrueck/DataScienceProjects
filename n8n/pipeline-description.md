## Pipeline Overview

This project follows a **multi-stage analysis pipeline** designed to identify and classify real-world usage of n8n based solely on publicly available repository metadata and README content.

Each stage has a clearly defined responsibility and produces explicit, inspectable outputs.

---

### 1. Repository Discovery

**Purpose**
Identify GitHub repositories that are likely to **deploy and use n8n in practice**, rather than merely documenting it or extending it via plugins.

**Method**
The pipeline queries the GitHub Search API for repositories containing **deployment-related artefacts**, such as:

* `docker-compose.yml` files referencing the `n8nio/n8n` image,
* Kubernetes or Helm manifests that deploy n8n,
* environment variable definitions commonly used by n8n.

Multiple targeted queries are executed and merged to form a high-precision candidate set.

**Output**
A de-duplicated list of candidate repositories that strongly indicate n8n deployment.

---

### 2. Repository Scoring and Filtering

**Purpose**
Ensure that discovered repositories represent **actual n8n usage**, not plugins, tutorials, or infrastructure templates.

**Method**
Each candidate repository is inspected using lightweight heuristics:

* positive signals for deployment artefacts and configuration files,
* negative signals for plugin-style repositories (e.g. custom node development).

A simple scoring model is applied to balance these signals.

**Output**
A filtered set of repositories classified as **n8n usage repositories**, suitable for semantic analysis.

---

### 3. Content Extraction

**Purpose**
Collect only the information required for **semantic classification**, while keeping the process efficient and reproducible.

**Method**
For each usage repository, the pipeline extracts:

* the repository README,
* the top-level directory and file structure,
* a small set of high-signal configuration files when present.

No full repository clones are required.

**Output**
A compact, structured representation of each repository’s descriptive content.

---

### 4. README-Based Semantic Classification

**Purpose**
Determine **what kind of software is being built with n8n**, based exclusively on how the project describes itself.

**Method**
An LLM is prompted with:

* the repository README,
* the high-level file structure,
* a fixed JSON classification schema.

The model assigns labels across multiple dimensions, including:

* project context (e.g. internal tool, product, example),
* target market and user groups,
* primary and secondary software domains,
* use-case specificity.

All labels must be selected from predefined categories, with uncertainty explicitly marked.

**Output**
A validated JSON object containing structured classifications, supporting evidence quotes, and confidence scores.

---

### 5. Validation and Storage

**Purpose**
Guarantee consistent, machine-readable results suitable for aggregation and analysis.

**Method**
LLM outputs are validated against a strict schema. Invalid responses are automatically retried once with corrective instructions.

Validated results are stored in a normalized format (e.g. JSON Lines).

**Output**
A clean, analyzable dataset of classified n8n usage repositories.

---

### 6. Aggregation and Analysis

**Purpose**
Answer the core research question:
**For which types of software is n8n used in practice?**

**Method**
The classified dataset is aggregated to analyze:

* distribution of software domains,
* prevalence of internal tools vs. customer-facing products,
* common target user groups,
* patterns in use-case granularity.

Results can be visualized or exported for further study.

**Output**
Quantitative and qualitative insights into real-world n8n adoption patterns.

---

### Design Principles

* **Precision over recall**: the pipeline favors reliable usage signals over exhaustive coverage.
* **Explainability**: all classifications are traceable to README evidence.
* **Reproducibility**: every stage produces explicit, inspectable outputs.
* **Scope discipline**: only information available from repository descriptions is used for semantic inference.
