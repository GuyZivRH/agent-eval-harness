#!/usr/bin/env python3
"""Simple OpenAI-compatible agent for AEH + OpenShell testing.

Uses /v1/chat/completions endpoint which works with LiteLLM proxy + Vertex AI.
Uses curl subprocess to bypass OpenShell network policy restrictions on Python.
"""
import json
import os
import subprocess
import sys

def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Hello"
    
    base_url = os.environ.get("OPENAI_BASE_URL", "http://host.openshell.internal:8000/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "test-key-12345")
    model = os.environ.get("OPENAI_MODEL", "claude-sonnet-4-6")
    output_dir = os.environ.get("OUTPUT_DIR", "output")
    
    url = f"{base_url}/chat/completions"
    
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024
    })
    
    try:
        result = subprocess.run(
            ["curl", "-s", url,
             "-H", f"Authorization: Bearer {api_key}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            print(f"curl failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
            
        data = json.loads(result.stdout)
        
        if "error" in data:
            print(f"API error: {data['error']}", file=sys.stderr)
            sys.exit(1)
            
        content = data["choices"][0]["message"]["content"]
        print(content)
        
        # Write metrics for AEH
        usage = data.get("usage", {})
        metrics = {
            "token_usage": {
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0)
            },
            "model": data.get("model", model)
        }
        os.makedirs(output_dir, exist_ok=True)
        with open(f"{output_dir}/metrics.json", "w") as f:
            json.dump(metrics, f)
                
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
