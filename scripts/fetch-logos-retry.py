import urllib.request, urllib.parse, urllib.error, json, os, time, subprocess, re

os.makedirs("/root/agy-logos", exist_ok=True)

def is_valid_image(filepath):
    try:
        with open(filepath, 'rb') as f:
            header = f.read(12)
            if header.startswith(b'\xff\xd8\xff'): return True
            if header.startswith(b'\x89PNG\r\n\x1a\n'): return True
            if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'): return True
            if header[0:4] == b'RIFF' and header[8:12] == b'WEBP': return True
    except:
        pass
    return False

def convert_to_png(filepath):
    try:
        subprocess.run(['convert', filepath, f"{filepath}.png"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.rename(f"{filepath}.png", filepath)
        return True
    except:
        return False

def http_get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
    while True:
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"429 Too Many Requests for {url}, sleeping 5 seconds...")
                time.sleep(5)
            else:
                return None
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return None

def search_wiki(query, wiki_domain):
    url = f"https://{wiki_domain}/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query)}&utf8=&format=json&srnamespace=6"
    data = http_get(url)
    if data:
        try:
            parsed = json.loads(data.decode())
            if parsed['query']['search']:
                return parsed['query']['search'][0]['title']
        except:
            pass
    return None

def get_image_url(filename, wiki_domain):
    url = f"https://{wiki_domain}/w/api.php?action=query&titles={urllib.parse.quote(filename)}&prop=imageinfo&iiprop=url&iiurlwidth=500&format=json"
    data = http_get(url)
    if data:
        try:
            parsed = json.loads(data.decode())
            pages = parsed['query']['pages']
            for page_id in pages:
                if 'imageinfo' in pages[page_id]:
                    ii = pages[page_id]['imageinfo'][0]
                    if filename.lower().endswith('.svg'):
                        return ii.get('thumburl', ii.get('url'))
                    return ii.get('url')
        except:
            pass
    return None

def download_image(url, out_path):
    data = http_get(url)
    if data and len(data) >= 500:
        try:
            with open(out_path, 'wb') as f:
                f.write(data)
            return True
        except:
            return False
    return False

# parse channels, filter out SOLVED
solved_ids = set()
try:
    with open("/root/agy-logos/REPORT.md", "r") as f:
        for line in f:
            if line.startswith("SOLVED:") or line.startswith("UNCERTAIN:"):
                name = line.split("->")[0].split(":")[1].strip()
                solved_ids.add(name)
except:
    pass

channels = []
with open("/root/agy_channels.txt", "r") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        parts = line.split('\t')
        if len(parts) >= 2:
            name_raw = parts[0]
            cid = parts[1]
            name = name_raw.replace("GO: ", "").strip()
            channels.append((name, cid))

report_lines = []
try:
    with open("/root/agy-logos/REPORT.md", "r") as f:
        for line in f:
            if line.startswith("SOLVED:") or line.startswith("UNCERTAIN:"):
                report_lines.append(line.strip())
except:
    pass

def log_result(status, name, info):
    line = f"{status}: {name} -> {info}"
    print(line)
    report_lines.append(line)

for name, cid in channels:
    if name in solved_ids:
        continue

    wiki_domains = ["commons.wikimedia.org", "en.wikipedia.org"]
    query = f"{name} logo"
    filename = None
    found_domain = None
    
    for domain in wiki_domains:
        time.sleep(1) # Rate limit protection
        filename = search_wiki(query, domain)
        if filename:
            found_domain = domain
            break
            
    if filename:
        time.sleep(1)
        img_url = get_image_url(filename, found_domain)
        if img_url:
            out_path = f"/root/agy-logos/{cid}.png"
            time.sleep(1)
            if download_image(img_url, out_path):
                if is_valid_image(out_path):
                    convert_to_png(out_path)
                    
                    # Heuristic check
                    words = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', name) if len(w) > 2]
                    fn_lower = filename.lower()
                    match_count = sum(1 for w in words if w in fn_lower)
                    
                    if match_count > 0 or len(words) == 0:
                        log_result("SOLVED", name, img_url)
                    else:
                        log_result("UNCERTAIN", name, f"Found {img_url} but filename '{filename}' seems unrelated to '{name}'.")
                    continue
                else:
                    os.remove(out_path)
                    
    log_result("NO_GOOD_SOURCE", name, "Could not find a matching logo on Wikimedia Commons or English Wikipedia.")

with open("/root/agy-logos/REPORT.md", "w") as f:
    for line in report_lines:
        f.write(line + "\n")
