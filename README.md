# Purdue ROV PCB DevOps

This repository houses the central CI/CD automation configurations, KiBot master profiles, and reusable GitHub Actions workflows for automated hardware validation (ERC/DRC) and fabrication package generation.

## Contents

- `kibot_master.yaml`: The master configuration file dictating preflight validation rules (ERC, DRC, zone fills) and manufacturing exports (PDF schematic, interactive HTML BOM, Gerber & Drill files).
- `.github/workflows/run-kicad-ci.yml`: A central reusable GitHub Actions workflow that board repositories reference to execute the KiBot pipeline.

## Usage in Board Repositories

To enable automated checks and fabrication exports on your board repository, add the following workflow file under `.github/workflows/ci.yml` in your board's repository:

```yaml
name: Hardware CI/CD Pipeline

on:
  push:
    branches: [ main, master, develop ]
  pull_request:
    branches: [ main, master, develop ]

jobs:
  run-validation:
    uses: purduerov/pcb-devops/.github/workflows/run-kicad-ci.yml@master
```
