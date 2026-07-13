#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import json
import os
import sys

# API Credentials
MOUSER_API_KEY = os.getenv("MOUSER_API_KEY")
DIGIKEY_CLIENT_ID = os.getenv("DIGIKEY_CLIENT_ID")
DIGIKEY_CLIENT_SECRET = os.getenv("DIGIKEY_CLIENT_SECRET")
DIGIKEY_TOKEN_PATH = os.getenv("DIGIKEY_TOKEN_PATH", "digikey_token.json")

if not MOUSER_API_KEY and not DIGIKEY_CLIENT_ID:
    print("Warning: No sourcing API credentials (MOUSER_API_KEY or DIGIKEY_CLIENT_ID) defined in environment. Sourcing check will be skipped.", file=sys.stderr)

def parse_kicad_xml_bom(xml_path):
    """Extract MPN, DigiKey part number, and designators from KiCad XML Bill of Materials"""
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
        digikey_pn = None
        if fields is not None:
            for field in fields.iter('field'):
                name = field.attrib.get('name')
                if name == 'MPN':
                    mpn = field.text
                elif name == 'DigiKey':
                    digikey_pn = field.text
        
        key_mpn = mpn.strip() if mpn else None
        key_digikey = digikey_pn.strip() if digikey_pn else None
        
        if key_mpn or key_digikey:
            key = (key_mpn, key_digikey)
            if key not in parts:
                parts[key] = []
            parts[key].append(ref)
            
    return parts

def get_digikey_access_token():
    """Retrieve DigiKey OAuth access token, refreshing if necessary"""
    if not DIGIKEY_CLIENT_ID or not DIGIKEY_CLIENT_SECRET:
        return None
        
    token_data = {}
    if os.path.exists(DIGIKEY_TOKEN_PATH):
        try:
            with open(DIGIKEY_TOKEN_PATH, 'r') as f:
                token_data = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load DigiKey token file: {e}", file=sys.stderr)
            
    refresh_token = token_data.get("refresh_token") or os.getenv("DIGIKEY_REFRESH_TOKEN")
    if not refresh_token:
        # Fallback to direct access token if available
        access_token = token_data.get("access_token") or os.getenv("DIGIKEY_ACCESS_TOKEN")
        if access_token:
            return access_token
        print("Warning: DIGIKEY_REFRESH_TOKEN or stored token JSON not available.", file=sys.stderr)
        return None
        
    url = "https://api.digikey.com/v1/oauth2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": DIGIKEY_CLIENT_ID,
        "client_secret": DIGIKEY_CLIENT_SECRET,
        "refresh_token": refresh_token
    }
    
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            new_token_data = {
                "access_token": res_data.get("access_token"),
                "refresh_token": res_data.get("refresh_token")
            }
            try:
                with open(DIGIKEY_TOKEN_PATH, 'w') as f:
                    json.dump(new_token_data, f)
            except Exception as e:
                print(f"Warning: Failed to save updated DigiKey token file: {e}", file=sys.stderr)
            return new_token_data["access_token"]
    except Exception as e:
        print(f"Error refreshing DigiKey access token: {e}", file=sys.stderr)
        # Fallback to current access token if available
        access_token = token_data.get("access_token") or os.getenv("DIGIKEY_ACCESS_TOKEN")
        if access_token:
            return access_token
            
    return None

def query_digikey_part_data(mpn, digikey_pn=None):
    """Programmatically fetch live pricing and stock levels from DigiKey API v4"""
    access_token = get_digikey_access_token()
    if not access_token:
        return None
        
    domain = "sandbox-api.digikey.com" if os.getenv("DIGIKEY_USE_SANDBOX") == "true" else "api.digikey.com"
    url = f"https://{domain}/products/v4/search/keyword"
    
    query = digikey_pn if digikey_pn else mpn
    if not query:
        return None
        
    payload = {
        "Keywords": query,
        "RecordCount": 1,
        "ExcludeMarketPlaceProducts": True
    }
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-DIGIKEY-Client-Id": DIGIKEY_CLIENT_ID,
        "Content-Type": "application/json",
        "X-DIGIKEY-Locale-Site": os.getenv("DIGIKEY_LOCALE_SITE", "US"),
        "X-DIGIKEY-Locale-Currency": os.getenv("DIGIKEY_LOCALE_CURRENCY", "USD"),
        "X-DIGIKEY-Locale-Language": os.getenv("DIGIKEY_LOCALE_LANGUAGE", "en")
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            products = res_data.get("Products", [])
            if products:
                prod = products[0]
                status_obj = prod.get("ProductStatus", {})
                status_str = status_obj.get("Status", "Active")
                
                if prod.get("EndOfLife") or prod.get("Discontinued"):
                    status_str = "Obsolete"
                    
                price = "N/A"
                pricing = prod.get("StandardPricing", [])
                if pricing:
                    price = pricing[0].get("UnitPrice", "N/A")
                    
                return {
                    "stock": prod.get("QuantityAvailable", 0),
                    "price_tier_1": price,
                    "status": status_str,
                    "datasheet": prod.get("PrimaryDatasheetUrl", "") or prod.get("DatasheetUrl", "")
                }
    except Exception as e:
        print(f"Error executing DigiKey API call for {query}: {e}", file=sys.stderr)
        
    return None

def query_mouser_part_data(mpn):
    """Programmatically fetch live pricing and stock levels from Mouser API"""
    if not MOUSER_API_KEY:
        return None
        
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
        
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ./fetch_sourcing_bom.py <kicad_bom.xml>")
        sys.exit(1)
        
    xml_bom_file = sys.argv[1]
    bom_parts = parse_kicad_xml_bom(xml_bom_file)
    
    print("MPN,DigiKey_PN,Quantity,Designators,Stock,Unit_Cost,Lifecycle,Source")
    has_obsolete = False
    
    for (part_mpn, part_digikey), designators in bom_parts.items():
        qty = len(designators)
        refs = ";".join(designators)
        
        # Query API
        sourcing = None
        source_distributor = "None"
        
        # 1. Try DigiKey first if configured
        if DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET:
            sourcing = query_digikey_part_data(part_mpn, part_digikey)
            if sourcing:
                source_distributor = "DigiKey"
                
        # 2. Try Mouser as fallback
        if not sourcing and MOUSER_API_KEY:
            sourcing = query_mouser_part_data(part_mpn)
            if sourcing:
                source_distributor = "Mouser"
                
        # 3. Fallback if neither succeeded/configured
        if not sourcing:
            sourcing = {"stock": "UNKNOWN", "price_tier_1": "UNKNOWN", "status": "UNKNOWN", "datasheet": ""}
            
        # Check lifecycle warnings
        status = sourcing["status"]
        if status in ["Obsolete", "End of Life", "EOL", "NRND", "Discontinued"]:
            part_display = part_digikey if part_digikey else part_mpn
            print(f"⚠️ CRITICAL WARNING: Part {part_display} used in {refs} is reported as {status}!", file=sys.stderr)
            has_obsolete = True
            
        print(f'"{part_mpn or ""}","{part_digikey or ""}",{qty},"{refs}",{sourcing["stock"]},"{sourcing["price_tier_1"]}","{sourcing["status"]}","{source_distributor}"')
        
    if has_obsolete:
        print("\nSourcing warning: One or more components are End of Life / Obsolete.", file=sys.stderr)
