# Purdue ROV PCB DevOps

This repository houses the central CI/CD automation configurations, KiBot master profiles, symbol linter validators, component sourcing scripts, and reusable GitHub Actions workflows for hardware validation (ERC/DRC) and fabrication package generation.

---

## 📂 Repository Contents

### 1. Configurations
* **[kibot_master.yaml](kibot_master.yaml)**: Master configuration file dictating preflight validation rules (ERC, ERC warnings, zone fills check, with `run_drc: false` for early-stage layout flexibility) and manufacturing exports (PDF schematic, interactive HTML BOM, Gerber & Drill files).

### 2. Validation & Sourcing Scripts (`scripts/`)
* **[scripts/linter_validator.py](scripts/linter_validator.py)**: Central Python linter script verifying mandatory symbol fields (`MPN`, `Manufacturer`, `Datasheet`, `Temp_Range`, `DigiKey`, `Category`). Supports CLI wildcards and skips graphic sub-symbols (e.g. `_0_0`).
* **[scripts/fetch_sourcing_bom.py](scripts/fetch_sourcing_bom.py)**: Component sourcing script querying distributor APIs:
  * **DigiKey v4 Product Search API** (OAuth2 token refresh caching in `digikey_token.json`).
  * **Mouser Search API**.
  * **Lifecycle & Stock Alerts:** Warns if parts are EOL, NRND, or out of stock.
* **[scripts/visual_diff.sh](scripts/visual_diff.sh)**: Visual diff tool generating pixel-by-pixel comparisons of copper layout layers between git branches using `kicad-cli` and ImageMagick.

### 3. Workflows (`.github/workflows/`)
* **[.github/workflows/run-kicad-ci.yml](.github/workflows/run-kicad-ci.yml)**: Central reusable CI pipeline executed by board repositories to run preflight validation, central linter, sourcing checks, and fabrication artifact generation.
* **[.github/workflows/devops-ci.yml](.github/workflows/devops-ci.yml)**: Self-validation pipeline testing Python scripts and verifying `kibot_master.yaml` YAML syntax on every push or PR.

---

## 🚀 Integration in Board Repositories

To enable automated CI checks in any board repository, create `.github/workflows/ci.yml` in your board repository:

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

## 🔒 Environment Secrets

For component stock and lifecycle checks in cloud CI:

* **Mouser:** Add secret `MOUSER_API_KEY`.
* **DigiKey:** Add secrets `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`, `DIGIKEY_REFRESH_TOKEN`.
