#!/usr/bin/env bash
# sync-health.sh
# 驗證 Claude Code 跨裝置同步架構是否健康（Windows 版）
# 由 pro-kit 07 生成

set -e

echo "🩺 sync-health.sh 開始體檢..."
echo ""

FAIL=0
CLAUDE_DIR="$USERPROFILE/.claude"
# Git Bash 路徑格式
CLAUDE_DIR_BASH="$HOME/.claude"

# 檢查 1：~/.claude/ 底下的 symlink 指向是否都存在
echo "[1/4] 檢查 ~/.claude/ symlink..."
for item in settings.json CLAUDE.md hooks statusline-command.sh skills; do
  link="$CLAUDE_DIR_BASH/$item"
  if [ -L "$link" ]; then
    target=$(readlink "$link")
    if [ -e "$target" ]; then
      echo "  ✅ $item → $target"
    else
      echo "  ❌ $item → $target（target 不存在）"
      FAIL=$((FAIL+1))
    fi
  elif [ -e "$link" ]; then
    echo "  ⚠️  $item 是一般檔案（不是 symlink，可能是 OneDrive 把 symlink 吃掉了）"
    FAIL=$((FAIL+1))
  else
    echo "  ⚠️  $item 不存在"
  fi
done

# 檢查 2：關鍵 skill 讀得到
echo ""
echo "[2/4] 檢查關鍵 skill 可讀取..."
for skill in skill-creator pif; do
  TEST_SKILL="$CLAUDE_DIR_BASH/skills/$skill/SKILL.md"
  if [ -f "$TEST_SKILL" ]; then
    echo "  ✅ $skill/SKILL.md 可讀取"
  else
    echo "  ⚠️  $skill/SKILL.md 讀不到"
  fi
done

# 檢查 3：MEMORY.md 可讀取
echo ""
echo "[3/4] 檢查記憶系統..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEMORY="$SCRIPT_DIR/../memory/MEMORY.md"
if [ -f "$MEMORY" ]; then
  LINE_COUNT=$(wc -l < "$MEMORY")
  echo "  ✅ MEMORY.md 可讀取（$LINE_COUNT 行）"
else
  echo "  ❌ MEMORY.md 讀不到：$MEMORY"
  FAIL=$((FAIL+1))
fi

# 檢查 4：OneDrive 母體資料夾存在
echo ""
echo "[4/4] 檢查 OneDrive 母體資料夾..."
MOTHER="/d/OneDrive/00 Claude Code/000_Agent/.claude"
if [ -d "$MOTHER" ]; then
  FILE_COUNT=$(find "$MOTHER" -type f 2>/dev/null | wc -l)
  echo "  ✅ 母體 .claude/ 存在（$FILE_COUNT 個檔案）"
else
  echo "  ❌ 母體 .claude/ 不存在：$MOTHER"
  FAIL=$((FAIL+1))
fi

echo ""
echo "========================================"
if [ "$FAIL" = "0" ]; then
  echo "🎉 全部正常！你的 AI 分身活著。"
  echo "   $(date '+%Y-%m-%d %H:%M:%S') - 體檢通過"
else
  echo "⚠️  發現 $FAIL 個問題，建議從 ~/claude-backup-* 檢查或重跑 pro-kit 07。"
  echo "   $(date '+%Y-%m-%d %H:%M:%S') - 體檢失敗"
  exit 1
fi
