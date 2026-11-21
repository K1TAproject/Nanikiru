# Git 日常同步

2026-11-20 核查：远程 `origin` 为 `https://github.com/K1TAproject/Nanikiru.git`；本地 `main` 已跟踪 `origin/main`。已有本地提交身份，无需重新初始化仓库或重新添加远程。本轮只调整仓库文件，不代替用户暂存、提交或推送。

在仓库根目录执行：

```powershell
cd D:\Nanikiru
git add .
git diff --cached --stat
git commit -m "同步新架构与分析研发进展"
git push
```

其中 `git diff --cached --stat` 是提交前的可选核对。`git add .` 会包含根目录下的新增、修改和删除；本次大量旧文件删除是之前架构调整的工作区现状，提交后 GitHub 才会体现这些删除。本轮没有重新删除这些文件。提交说明需要填写实际内容，不使用空字符串。

## 哪些内容会同步

源码 `nanikiru/`，各 UI 目录和启动入口，`tests/` 及小型 fixtures，`examples/`，`tools/` 构建脚本，`assets/` 资源及说明，项目文档、研发记录、证据摘要、依赖清单、Git 规则。没有对整个 JSON、PNG 或所有数据库扩展名做全局忽略，避免误排除真正的测试资源。

`.gitattributes` 让文本源文件在提交和检出时使用 LF，Windows 批处理使用 CRLF；二进制文件由 Git 自动识别。无需改其他项目的全局 Git 设置。

## 哪些内容仅保留在本机

- `/data/` 全目录：原始牌谱、训练特征与数据库、权重、对战结果、第三方机器人源码及构建产物。
- `docs/observation-checks/source.jsonl` 与该目录下的 `observations/`：可重新生成的观察器样本；`report.md` 仍同步。
- Python 虚拟环境、缓存、构建产物、IDE／代码索引目录、`.env` 等已有本地配置。
- 根目录 UI 默认存档 `simulation.json`、`manual_round.json`；`examples/` 中的版本化示例不受影响。

已有 `.serena/project.yml` 共享项目配置继续跟踪，其缓存与个人覆盖文件由 `.serena/.gitignore` 排除；不需要取消跟踪项目配置。

这些忽略规则不会删除本地文件。原始实验数据仍需单独备份；仅克隆 GitHub 不会取得历史权重、牌谱或 akochan 二进制。文档中的本地实验指针保留用于追溯，但在新电脑上需另恢复对应数据。新产生的数据与任意命名存档请统一放入 `data/`；真正需要纳入仓库的精选实验摘要放在 `docs/`。

## 验证与常见情况

```powershell
git status --short
git add --dry-run .
git check-ignore -v data/external-replays/example.jsonl
git branch -vv
```

如果远端后来增加了本地没有的提交，先保存／提交本地工作，再执行 `git pull --rebase`，解决可能的冲突后再推送。不要用强制推送覆盖远端历史。远程读取成功不代表已验证推送权限；若 HTTPS 推送要求登录，按 Git Credential Manager 的提示登录有仓库权限的 GitHub 账号即可，不在源码或远程 URL 中填写令牌。
