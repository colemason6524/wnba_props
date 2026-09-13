#!/usr/bin/env bash
# Pull-only sync of runtime outputs from the Azure VM to this Mac.
# Code deploys stay git-based (git pull on the VM); secrets never move.
set -u

VM_KEY=${VM_SSH_KEY:-"$HOME/Downloads/RunThemScripts_key.pem"}
VM_HOST=${VM_HOST:-"azureuser@130.131.0.6"}
VM_WNBA=${VM_WNBA_DIR:-"wnba_props"}
VM_SHADOW=${VM_SHADOW_DIR:-"wnba_props_shadow"}
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DRY_RUN=${DRY_RUN:-0}

SSH_OPTS="-i $VM_KEY -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
RSYNC_OPTS="-az --prune-empty-dirs"
if [[ "$DRY_RUN" == "1" ]]; then
    RSYNC_OPTS="$RSYNC_OPTS --dry-run"
fi

pull() {
    local src=$1
    local dest=$2
    mkdir -p "$dest"
    # shellcheck disable=SC2086
    rsync $RSYNC_OPTS -e "ssh $SSH_OPTS" "$VM_HOST:$src" "$dest"
}

pull "$VM_WNBA/outputs/history/" "$REPO_ROOT/outputs/history/"
pull "$VM_WNBA/outputs/health/" "$REPO_ROOT/outputs/health/"
pull "$VM_WNBA/outputs/logs/" "$REPO_ROOT/outputs/vm-logs/"
pull "$VM_WNBA/outputs/forecast_boards/" "$REPO_ROOT/outputs/forecast_boards/"
pull "$VM_WNBA/outputs/ledger/" "$REPO_ROOT/outputs/ledger/"
pull "$VM_WNBA/outputs/grades/" "$REPO_ROOT/outputs/grades/"
pull "$VM_SHADOW/outputs/" "$REPO_ROOT/outputs/shadow-vm/"

printf 'Sync complete: history=%s health=%s vm-logs=%s boards=%s grades=%s shadow-vm=%s\n' \
    "$(find "$REPO_ROOT/outputs/history" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')" \
    "$(find "$REPO_ROOT/outputs/health" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')" \
    "$(find "$REPO_ROOT/outputs/vm-logs" -type f 2>/dev/null | wc -l | tr -d ' ')" \
    "$(find "$REPO_ROOT/outputs/forecast_boards" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')" \
    "$(find "$REPO_ROOT/outputs/grades" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')" \
    "$(find "$REPO_ROOT/outputs/shadow-vm" -type f 2>/dev/null | wc -l | tr -d ' ')"
