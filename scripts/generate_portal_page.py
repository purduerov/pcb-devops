#!/usr/bin/env python3
"""
Generates a modern, mobile-friendly landing page (index.html) in the outputs directory
for GitHub Pages deployment, featuring 1-click access to Interactive HTML BOM,
Schematic PDFs, Layout PDFs, and component summaries.
"""

import os
import sys
import glob

def generate_portal(output_dir, repo_name="Purdue ROV Hardware", commit_sha="", branch="master"):
    if not os.path.isdir(output_dir):
        print(f"Error: Output directory not found: {output_dir}", file=sys.stderr)
        return

    # Find generated files
    iboms = glob.glob(os.path.join(output_dir, "*-iBOM.html")) + glob.glob(os.path.join(output_dir, "*_ibom.html"))
    sch_pdfs = glob.glob(os.path.join(output_dir, "*-Schematic.pdf")) + glob.glob(os.path.join(output_dir, "*_schematic.pdf"))
    layout_pdfs = glob.glob(os.path.join(output_dir, "*-Layout.pdf")) + glob.glob(os.path.join(output_dir, "*_layout.pdf"))
    boms = glob.glob(os.path.join(output_dir, "*-BOM.csv"))

    ibom_link = os.path.basename(iboms[0]) if iboms else None
    sch_link = os.path.basename(sch_pdfs[0]) if sch_pdfs else None
    layout_link = os.path.basename(layout_pdfs[0]) if layout_pdfs else None
    bom_link = os.path.basename(boms[0]) if boms else None

    # If only iBOM exists and no other docs, we can redirect or show the portal
    # Portal with dark-mode Purdue ROV aesthetic:
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{repo_name} | Hardware Design Portal</title>
    <style>
        :root {{
            --bg: #0f111a;
            --surface: #1e222d;
            --surface-hover: #282e3d;
            --accent: #cfb991; /* Purdue Gold */
            --accent-hover: #e5cf9f;
            --text: #f0f3f6;
            --text-muted: #8b949e;
            --border: #30363d;
            --card-radius: 12px;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 2rem 1rem;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            box-sizing: border-box;
        }}
        .container {{
            max-width: 800px;
            width: 100%;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--card-radius);
            padding: 2.5rem;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.4);
        }}
        .header {{
            text-align: center;
            margin-bottom: 2rem;
            border-bottom: 1px solid var(--border);
            padding-bottom: 1.5rem;
        }}
        .header h1 {{
            margin: 0 0 0.5rem 0;
            color: var(--accent);
            font-size: 1.8rem;
            font-weight: 700;
        }}
        .header p {{
            margin: 0;
            color: var(--text-muted);
            font-size: 0.95rem;
        }}
        .meta-badges {{
            display: flex;
            justify-content: center;
            gap: 0.5rem;
            margin-top: 1rem;
            flex-wrap: wrap;
        }}
        .badge {{
            background: #161b22;
            border: 1px solid var(--border);
            padding: 0.25rem 0.6rem;
            border-radius: 6px;
            font-size: 0.8rem;
            color: var(--text-muted);
        }}
        .cards-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: #161b22;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.25rem;
            text-decoration: none;
            color: var(--text);
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
            transition: all 0.2s ease;
        }}
        .card:hover {{
            background: var(--surface-hover);
            border-color: var(--accent);
            transform: translateY(-2px);
        }}
        .card-icon {{
            font-size: 2rem;
            margin-bottom: 0.75rem;
        }}
        .card-title {{
            font-weight: 600;
            font-size: 1.1rem;
            margin-bottom: 0.25rem;
            color: var(--accent);
        }}
        .card-desc {{
            font-size: 0.85rem;
            color: var(--text-muted);
        }}
        .featured {{
            border-color: var(--accent);
            background: linear-gradient(180deg, rgba(207, 185, 145, 0.08) 0%, #161b22 100%);
        }}
        .footer {{
            text-align: center;
            font-size: 0.8rem;
            color: var(--text-muted);
            border-top: 1px solid var(--border);
            padding-top: 1.25rem;
        }}
        .footer a {{
            color: var(--accent);
            text-decoration: none;
        }}
        .footer a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌊 {repo_name}</h1>
            <p>Automated Hardware Design & Assembly Portal</p>
            <div class="meta-badges">
                <span class="badge">Branch: <strong>{branch}</strong></span>
                {f'<span class="badge">Commit: <code>{commit_sha[:7]}</code></span>' if commit_sha else ''}
                <span class="badge">Built via <strong>KiBot CI</strong></span>
            </div>
        </div>

        <div class="cards-grid">
"""

    if ibom_link:
        html_content += f"""
            <a href="{ibom_link}" class="card featured" target="_blank">
                <div class="card-icon">🔬</div>
                <div class="card-title">Interactive BOM</div>
                <div class="card-desc">Interactive assembly & soldering viewer with highlighted component pads</div>
            </a>
"""

    if sch_link:
        html_content += f"""
            <a href="{sch_link}" class="card" target="_blank">
                <div class="card-icon">📄</div>
                <div class="card-title">Schematic PDF</div>
                <div class="card-desc">Vector schematic sheets with complete pinouts and nets</div>
            </a>
"""

    if layout_link:
        html_content += f"""
            <a href="{layout_link}" class="card" target="_blank">
                <div class="card-icon">📐</div>
                <div class="card-title">PCB Layout PDF</div>
                <div class="card-desc">Full 2D board layer traces, silkinscreen, and placement drawings</div>
            </a>
"""

    if bom_link:
        html_content += f"""
            <a href="{bom_link}" class="card" download>
                <div class="card-icon">📊</div>
                <div class="card-title">BOM Spreadsheet</div>
                <div class="card-desc">Download CSV parts list with MPN, DigiKey, and manufacturer data</div>
            </a>
"""

    html_content += """
        </div>

        <div class="footer">
            Purdue ROV Electrical Engineering &bull; Powered by <a href="https://github.com/purduerov/pcb-devops" target="_blank">pcb-devops</a>
        </div>
    </div>
</body>
</html>
"""

    index_path = os.path.join(output_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Generated GitHub Pages portal at: {index_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: generate_portal_page.py <output_directory> [repo_name] [commit_sha] [branch]")
        sys.exit(1)
    
    out_dir = sys.argv[1]
    repo = sys.argv[2] if len(sys.argv) > 2 else "Purdue ROV Board"
    sha = sys.argv[3] if len(sys.argv) > 3 else ""
    br = sys.argv[4] if len(sys.argv) > 4 else "master"

    generate_portal(out_dir, repo, sha, br)
