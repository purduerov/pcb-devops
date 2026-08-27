# Purdue ROV PCB DevOps

Central automation and CI/CD tools for Purdue ROV hardware designs. This repository contains shared KiBot export profiles, symbol linting scripts, distributor sourcing checks, visual layout diffing tools, and reusable GitHub Actions workflows.

## Repository Contents

### 1. KiBot Configuration
- `kibot_master.yaml`: Shared KiBot configuration defining preflight validation (ERC, ERC warnings, zone fill checks) and manufacturing exports (PDF schematics, Interactive HTML BOM, Gerbers, and drill files).

### 2. Automation Scripts (`scripts/`)
- `linter_validator.py`: Checks `.kicad_sym` files for required fields (`Category`, `MPN`, `Manufacturer`, `Datasheet`, `Temp_Range`, `DigiKey`).
- `fetch_sourcing_bom.py`: Queries DigiKey and Mouser APIs for component lifecycle status (EOL, NRND) and real-time inventory.
- `visual_diff.sh`: Renders and compares PCB copper layers between git branches using `kicad-cli` and ImageMagick.
- `sync_template_to_all.ps1` / `sync_template_to_all.sh`: Propagates shared scripts and configuration updates across team board repositories.

### 3. Reusable Workflows (`.github/workflows/`)
- `run-kicad-ci.yml`: Reusable workflow called by board repositories to run preflight validation, linting, sourcing checks, and manufacturing exports.
- `devops-ci.yml`: CI workflow validating Python scripts and YAML syntax for this repository.

## Using CI in Board Repositories

To run automated checks on PRs and pushes in a board repository, add `.github/workflows/ci.yml`:

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

## Secrets Configuration

For component stock and lifecycle checks in GitHub Actions:
- **DigiKey:** Add `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`, and `DIGIKEY_REFRESH_TOKEN` to repository secrets.
- **Mouser:** Add `MOUSER_API_KEY` to repository secrets.
