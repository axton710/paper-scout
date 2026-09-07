#!/bin/sh
# 用仓库源码启动 dsh（经 tsx 的 ESM 钩子），这样所有 workspace 插件（含 mcp-client）都可解析。
# 进程 cwd 由 SDK 的 runtime_cwd 指到仓库根，tsx/esm 才能被解析；--profile/--patch 参数透传。
: "${DEEPSEEK_HARNESS_REPO:?请设置 DEEPSEEK_HARNESS_REPO 为 Harness 源码目录}"
exec node --import tsx/esm "$DEEPSEEK_HARNESS_REPO/apps/cli/src/bin.ts" "$@"
