# 贡献指南（CONTRIBUTING）

感谢你关注 **AI-Lab**！本仓库是面向 Apple Silicon 的隐私优先本地 AI 实验室参考配置，仅包含示例、脚本与小工具。

## 基本原则

1. **绝不提交敏感物**：日志（`.log`）、PDF 原始数据、模型权重、API Key、`02_data/`、个人 `rule.md`、`.workbuddy/` 等一律不入库。`.gitignore` 已兜底，但请自觉核对。
2. **不臆造命令/路径**：所有脚本与文档中的命令、端口、模型名、路径应来自真实环境，或明确标注为「示例 / 占位」。
3. **中文优先**：仓库文档与代码注释以简体中文为主；新增英文文档请放在 `README.en.md` 这类独立文件，不要混进中文文档。
4. **零依赖优先**：脚本尽量只用 Python 标准库或已在 `requirements-*.txt` 锁定的依赖，降低上手成本。

## 本地校验（提交前请自查）

CI 会自动跑，但你也可以在本地先验证，避免来回：

```bash
# Shell 语法检查（示例脚本）
for f in scripts/*.sh.example launcher/*.command.example; do bash -n "$f"; done

# Python 编译检查
for f in scripts/*.py monitor/*.py; do python3 -m py_compile "$f"; done

# JSON 合法性
for f in $(find . -path ./.git -prune -o -name '*.json' -print); do python3 -c "import json; json.load(open('$f'))"; done
```

## 提交规范

- 分支：从 `main` 切出 `feat/...`、`fix/...`、`docs/...` 等描述性分支。
- 提交信息：用祈使句，中文即可，例如「docs: 补充容器穿透的中继双验证说明」。
- PR：描述清楚「改了什么 / 为什么 / 是否影响安全姿态（回环、鉴权、暴露面）」。

## 安全相关改动

任何涉及网络绑定、鉴权、暴露面的改动，请在 PR 说明里显式回答：

- 是否仍仅绑定回环（`127.0.0.1`）？
- 是否引入了新依赖或新端口？
- 密钥是否仍只从文件读取、不落库、不进日志？

## 新增脚本 checklist

- [ ] 放在合适的目录（`scripts/` 通用工具、`launcher/` 启动器模板、`monitor/` 监控、`profiles/` 客户端画像）。
- [ ] 含完整中文注释，解释「为什么」而非「做了什么」。
- [ ] 个人绝对路径改为占位符或环境变量。
- [ ] 已在本地通过上面的校验。
