# skills/ — 你的 AI 工作手冊

這個資料夾放「AI 遇到某類任務的完整 How-to」。每個子資料夾是一個 skill，裡面必須有一個 `SKILL.md`。

## 你的第一個 skill 怎麼建？

**方法 1（推薦）**：裝官方 `skill-creator`，然後在對話裡說：
> `/skill-creator` 幫我建一個 skill，我想讓 AI 自動 [具體任務]

**方法 2**：手動建一個子資料夾 + `SKILL.md`，frontmatter 至少要有 `name` 和 `description`：

```yaml
---
name: my-first-skill
description: 做某件事的時候會用到，觸發條件是...
---

（你的 SOP 寫在這裡）
```

建好之後你打 `/my-first-skill` 就會觸發。
