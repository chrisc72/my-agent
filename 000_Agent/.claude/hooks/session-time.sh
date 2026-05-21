#!/bin/bash
# ─────────────────────────────────────────────────────────
# Session Time Hook · Claude Code Starter Kit #06
# by 雷蒙（Raymond Hou）· https://cc.lifehacker.tw
# Source: https://github.com/Raymondhou0917/claude-code-resources
# License: CC BY-NC-SA 4.0
# ─────────────────────────────────────────────────────────
# 記錄本次 session 最後一則訊息的時間戳
# 供 ~/.claude/statusline-command.sh 讀取顯示
# 時區：Asia/Taipei
TZ="Asia/Taipei" date '+%Y-%m-%d %H:%M' > ~/.claude/last-session-msg
