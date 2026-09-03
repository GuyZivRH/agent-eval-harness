import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

def refresh():
    root = Path(".")
    out = root / ".tmp" / "forge-real"
    token_file = out / "m365-token.json"
    env_file = out / "env"
    
    if not token_file.exists():
        print(f"error: {token_file} missing")
        return False
        
    with open(token_file) as f:
        data = json.load(f)
        
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print("error: no refresh_token in m365-token.json")
        return False
        
    tid = os.environ.get("M365_TENANT_ID")
    cid = os.environ.get("M365_CLIENT_ID")
    sec = os.environ.get("M365_CLIENT_SECRET")
    
    if not all([tid, cid, sec]):
        # Try to load from env file
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    if line.startswith("export "):
                        line = line[7:]
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        if v.startswith("'") and v.endswith("'"): v = v[1:-1]
                        if v.startswith('"') and v.endswith('"'): v = v[1:-1]
                        os.environ[k] = v
            tid = os.environ.get("M365_TENANT_ID")
            cid = os.environ.get("M365_CLIENT_ID")
            sec = os.environ.get("M365_CLIENT_SECRET")

    if not all([tid, cid, sec]):
        print("error: missing M365_TENANT_ID, CLIENT_ID, or CLIENT_SECRET")
        return False

    body_dict = {
        "grant_type": "refresh_token",
        "client_id": cid,
        "refresh_token": refresh_token,
    }
    # For public clients (device flow), client_secret must NOT be sent.
    # if sec:
    #    body_dict["client_secret"] = sec
    body = urllib.parse.urlencode(body_dict).encode()
    
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
            
        data.update({
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token") or refresh_token,
            "expires_in": tok.get("expires_in"),
        })
        
        with open(token_file, "w") as f:
            json.dump(data, f, indent=2)
            
        if env_file.exists():
            lines = env_file.read_text().splitlines()
            new_lines = [l for l in lines if not l.startswith("export M365_ACCESS_TOKEN=")]
            new_lines.append(f"export M365_ACCESS_TOKEN={tok['access_token']!r}")
            env_file.write_text("\n".join(new_lines) + "\n")
            
        print("REFRESH_OK")
        return True
    except Exception as e:
        raw = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"REFRESH_FAILED: {raw}")
        return False

if __name__ == "__main__":
    refresh()
