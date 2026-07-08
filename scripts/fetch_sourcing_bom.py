#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import urllib.request
import json
import os
import sys

MOUSER_API_KEY = os.getenv("MOUSER_API_KEY")
if not MOUSER_API_KEY:
    print("Warning: MOUSER_API_KEY not defined in environment. Sourcing check will be skipped.", file=sys.stderr)

def parse_kicad_xml_bom(xml_path):
    """Extract MPN and designators from KiCad XML Bill of Materials"""
    parts = {}
    if not os.path.exists(xml_path):
        print(f"Error: XML BOM file not found: {xml_path}", file=sys.stderr)
        return parts

    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    # KiCad XML structure: components -> comp
    for comp in root.iter('comp'):
        ref = comp.attrib.get('ref')
        fields = comp.find('fields')
        mpn = None
        if fields is not None:
            for field in fields.iter('field'):
                if field.attrib.get('name') == 'MPN':
                    mpn = field.text
                    break
        if mpn:
            mpn = mpn.strip()
            if mpn not in parts:
                parts[mpn] = []
            parts[mpn].append(ref)
            
    return parts

def query_mouser_part_data(mpn):
    """Programmatically fetch live pricing and stock levels from Mouser API"""
    if not MOUSER_API_KEY:
        return {"stock": "UNKNOWN", "price_tier_1": "UNKNOWN", "status": "No API Key"}
        
    url = f"https://api.mouser.com/api/v1.0/search/partnumber?apiKey={MOUSER_API_KEY}"
    payload = {
        "SearchByPartRequest": {
            "mouserPartNumber": mpn,
            "partSearchOptions": "string"
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            results = res_data.get('SearchResults', {}).get('Parts', [])
            if results:
                part = results[0]
                price_breaks = part.get('PriceBreaks', [])
                price = "N/A"
                if price_breaks:
                    price = price_breaks[0].get('Price', "N/A")
                    
                return {
                    "stock": part.get('AvailabilityInStock', 0) or part.get('Availability', 0),
                    "price_tier_1": price,
                    "status": part.get('LifecycleStatus', 'Active') or "Active",
                    "datasheet": part.get('DatasheetUrl', '')
                }
    except Exception as e:
        print(f"Error executing Mouser API call for {mpn}: {e}", file=sys.stderr)
        
    return {"stock": 0, "price_tier_1": "N/A", "status": "ERROR"}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ./fetch_sourcing_bom.py <kicad_bom.xml>")
        sys.exit(1)
        
    xml_bom_file = sys.argv[1]
    bom_parts = parse_kicad_xml_bom(xml_bom_file)
    
    print("MPN,Quantity,Designators,Stock,Unit_Cost,Lifecycle")
    has_obsolete = False
    
    for part_mpn, designators in bom_parts.items():
        qty = len(designators)
        refs = ";".join(designators)
        
        # Query API
        sourcing = query_mouser_part_data(part_mpn)
        
        # Check lifecycle warnings
        status = sourcing["status"]
        if status in ["Obsolete", "End of Life", "EOL", "NRND"]:
            print(f"⚠️ CRITICAL WARNING: Part {part_mpn} used in {refs} is reported as {status}!", file=sys.stderr)
            has_obsolete = True
            
        print(f'"{part_mpn}",{qty},"{refs}",{sourcing["stock"]},"{sourcing["price_tier_1"]}","{sourcing["status"]}"')
        
    if has_obsolete:
        print("\nSourcing warning: One or more components are End of Life / Obsolete.", file=sys.stderr)
