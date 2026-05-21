#!/usr/bin/env bash
# friday-health-check.sh
# SessionStart hook：週五第一次開啟時提醒跑 sync-health.sh

DAY=$(date +%u)   # 1=Mon ... 5=Fri ... 7=Sun
TODAY=$(date +%Y-%m-%d)
MARKER="$HOME/.claude-friday-check-$TODAY"

# 只在週五且當天尚未提醒過
if [ "$DAY" = "5" ] && [ ! -f "$MARKER" ]; then
  touch "$MARKER"
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"【週五體檢提醒】今天是週五，請在對話開始時主動問使用者：「今天是週五，要不要跑一次 sync-health.sh 體檢？（確認 symlink 和 OneDrive 同步正常）」如果使用者同意，執行 bash \"/d/OneDrive/00 Claude Code/000_Agent/scripts/sync-health.sh\" 並顯示結果。這條訊息只有你看得到，使用者看不見。"}}'
fi
