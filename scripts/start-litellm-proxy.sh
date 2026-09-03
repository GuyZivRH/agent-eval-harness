#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${1:-$SCRIPT_DIR/litellm_config.yaml}"
PORT="${2:-8000}"
LOG_FILE="/tmp/litellm_openshell.log"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Starting LiteLLM Proxy for OpenShell...${NC}"

# Check for GCP credentials (Vertex AI)
if [ ! -f ~/.config/gcloud/application_default_credentials.json ]; then
    echo -e "${YELLOW}Warning: No GCP application default credentials found${NC}"
    echo "Run: gcloud auth application-default login"
fi

# Check for GOOGLE_CLOUD_PROJECT
if [ -z "$GOOGLE_CLOUD_PROJECT" ]; then
    # Try to get from gcloud config
    GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
    if [ -n "$GOOGLE_CLOUD_PROJECT" ]; then
        export GOOGLE_CLOUD_PROJECT
        echo -e "${GREEN}Using GCP project: $GOOGLE_CLOUD_PROJECT${NC}"
    else
        echo -e "${RED}Error: GOOGLE_CLOUD_PROJECT not set and couldn't detect from gcloud${NC}"
        echo "Set it with: export GOOGLE_CLOUD_PROJECT=your-project-id"
        exit 1
    fi
fi

# Check if config exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: Config file '$CONFIG_FILE' not found${NC}"
    exit 1
fi

# Kill existing proxy on this port
if lsof -ti :$PORT > /dev/null 2>&1; then
    echo -e "${YELLOW}Killing existing process on port $PORT...${NC}"
    kill $(lsof -ti :$PORT) 2>/dev/null || true
    sleep 2
fi

# Start proxy
echo -e "${GREEN}Starting proxy on 0.0.0.0:$PORT${NC}"
echo -e "${GREEN}Config: $CONFIG_FILE${NC}"

# Use uv run with litellm and google-cloud-aiplatform
if command -v uv &> /dev/null; then
    uv run --with "litellm[proxy]" --with "google-cloud-aiplatform>=1.38" litellm \
      --config "$CONFIG_FILE" \
      --host 0.0.0.0 \
      --port $PORT \
      --detailed_debug > "$LOG_FILE" 2>&1 &
else
    python3 -m litellm \
      --config "$CONFIG_FILE" \
      --host 0.0.0.0 \
      --port $PORT \
      --detailed_debug > "$LOG_FILE" 2>&1 &
fi

PROXY_PID=$!
echo -e "${GREEN}✓ Proxy started with PID: $PROXY_PID${NC}"
echo -e "${GREEN}✓ Logs: $LOG_FILE${NC}"

# Wait for startup
echo -e "${YELLOW}Waiting for proxy to start...${NC}"
sleep 5

# Test health
if curl -s http://localhost:$PORT/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Proxy is healthy${NC}"
else
    echo -e "${YELLOW}Warning: Health check didn't respond yet. Check logs: tail -f $LOG_FILE${NC}"
fi

# Get Podman host IP (from container perspective)
if command -v podman &> /dev/null; then
    GATEWAY_IP=$(podman run --rm alpine sh -c "getent hosts host.containers.internal 2>/dev/null | awk '{print \$1}'" 2>/dev/null || echo "")
    if [ -n "$GATEWAY_IP" ]; then
        echo ""
        echo -e "${GREEN}============================================${NC}"
        echo -e "${GREEN}Proxy ready for OpenShell sandboxes!${NC}"
        echo -e "${GREEN}============================================${NC}"
        echo ""
        echo -e "From sandbox containers, use:"
        echo -e "  ${YELLOW}ANTHROPIC_BASE_URL=http://$GATEWAY_IP:$PORT${NC}"
        echo -e "  ${YELLOW}ANTHROPIC_API_KEY=openshell-proxy-key${NC}"
        echo ""
        echo -e "To create OpenShell provider:"
        echo -e "  ${YELLOW}openshell provider create --name anthropic-proxy --type anthropic \\${NC}"
        echo -e "  ${YELLOW}  --credential ANTHROPIC_API_KEY=openshell-proxy-key \\${NC}"
        echo -e "  ${YELLOW}  --config api_base=http://$GATEWAY_IP:$PORT${NC}"
        echo ""
    fi
fi

echo -e "${GREEN}To stop: kill $PROXY_PID${NC}"
echo ""
echo "# Save this for later:"
echo "export LITELLM_PROXY_PID=$PROXY_PID"
