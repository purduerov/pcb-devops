# Purdue ROV PCB DevOps

This repository houses the central CI/CD automation configurations, KiBot master profiles, and reusable GitHub Actions workflows for automated hardware validation (ERC/DRC) and fabrication package generation.

---

## Contents

### 1. Configurations
*   **[kibot_master.yaml](kibot_master.yaml)**: The master configuration file dictating preflight validation rules (ERC, DRC, zone fills check) and manufacturing exports (PDF schematic, interactive HTML BOM, Gerber & Drill files).

### 2. Scripts (`scripts/`)
*   **[scripts/linter_validator.py](scripts/linter_validator.py)**: A Python script to lint KiCad symbol files for required metadata fields. It automatically ignores unit/graphic sub-symbols (e.g. `_0_0`).
*   **[scripts/fetch_sourcing_bom.py](scripts/fetch_sourcing_bom.py)**: A component sourcing check script that parses the generated XML BOM and queries distributor APIs:
    *   *DigiKey*: Integrates with the v4 Product Search API (using Client ID, Client Secret, and dynamic OAuth2 Refresh Tokens cached in a local `digikey_token.json` file).
    *   *Mouser*: Integrates with the Mouser Search API (using a static API key).
    *   *Lifecycle Warnings*: Warns if any parts are EOL, NRND, or Obsolete.
*   **[scripts/visual_diff.sh](scripts/visual_diff.sh)**: A shell script that generates pixel-by-pixel comparisons of copper layout layers between git branches using `kicad-cli` and ImageMagick.

### 3. Workflows (`.github/workflows/`)
*   **[.github/workflows/run-kicad-ci.yml](.github/workflows/run-kicad-ci.yml)**: Reusable central CI/CD workflow that board repositories reference to execute the KiBot preflight checks, sourcing checks, and artifact generations.
*   **[.github/workflows/devops-ci.yml](.github/workflows/devops-ci.yml)**: Self-validation pipeline that compiles Python scripts and checks `kibot_master.yaml` YAML syntax on every push or PR.

---

## Usage in Board Repositories

To enable automated checks and fabrication exports on your board repository, add the following workflow file under `.github/workflows/ci.yml` in your board's repository:

```yaml
name: Hardware CI/CD Pipeline

on:
  push:
    branches: [ master, main, develop ]
  pull_request:
    branches: [ master, main, develop ]

jobs:
  run-validation:
    uses: purduerov/pcb-devops/.github/workflows/run-kicad-ci.yml@master
```

---

## Configuring Sourcing Check Secrets

If your board repository needs automated sourcing checks (stock/lifecycle status), configure the following environment secrets in your repository settings:

### Mouser (Recommended for Stateless Cloud CI)
1.  Add `MOUSER_API_KEY` to your GitHub secrets.

### DigiKey (Requires Token Management)
1.  Register an app in the DigiKey Developer Portal.
2.  Add the following secrets to GitHub:
    *   `DIGIKEY_CLIENT_ID`
    *   `DIGIKEY_CLIENT_SECRET`
    *   `DIGIKEY_REFRESH_TOKEN` (initially generated locally and saved)
