

# 本目录用途

放 **PAT 凭证文件**（可选来源，优先级最低）：

- 文件名：`credential.local.json`
- 内容：`{"pat": "gdpat_…"}`
- 或改用环境变量 `BI_PAT_CREDENTIAL_FILE` 指到别处

> 令牌即账号权限：**绝不写入文档、日志或输出**；本目录默认不含任何凭证（`privacy`: 空的 `.gitkeep` 仅为占位）。
> 优先级：`--token` > `BI_PAT_TOKEN` > 本文件（见 `bi-pat/SKILL.md` §凭证）。
