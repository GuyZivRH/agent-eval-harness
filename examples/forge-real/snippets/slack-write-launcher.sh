#!/usr/bin/env sh
# From forge-outlook-slack profile. This file is the drafts↔writer bearer,
# NOT the Slack xoxp user token (that comes from OpenShell provider user_token).
set -eu
DRAFTS_SENDPATH_BEARER="$(cat /sandbox/persist/.forge-drafts/slack-drafts-bearer)"
export DRAFTS_SENDPATH_BEARER
exec /sandbox/slack-send-service
