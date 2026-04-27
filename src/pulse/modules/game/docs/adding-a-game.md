# Adding A Game

新增游戏不改 `GameModule` 代码。

## 1. 新建 YAML

复制 `games/_examples/template_game.yaml` 到 `games/<game_id>.yaml`。

必填字段:

| 字段 | 说明 |
|---|---|
| `id` | 小写下划线 id |
| `name` | 展示名 |
| `package_candidates` | Android 渠道包名候选 |
| `driver` | `adb_airtest` 或 `cloud_web` |
| `templates_dir` | 模板目录名 |
| `tasks` | 日常任务列表 |

## 2. 截取模板

模板放 `templates/<game_id>/`。按钮图只截可点击区域,避免包含动态文字、红点、资源数量。

## 3. 配置调度

`weekday_windows` / `weekend_windows` 使用整数小时半开区间,如 `[[9, 24]]`。默认 `enabled_by_default=false`,由 `system.patrol.enable` 开启。

## 4. dry-run 验证

先执行:

```bash
curl -X POST http://localhost:8000/api/modules/game/games/<game_id>/run \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

dry-run 至少完成一次后,再考虑把 YAML 的 `safety.default_dry_run` 改为 `false`。

## 5. 测试

新增或更新:

| 文件 | 覆盖 |
|---|---|
| `tests/fixtures/game/<game_id>/` | 脱敏截图 |
| `tests/pulse/modules/test_game_pipeline.py` | 模板识别、claim_chain、风险模板 |
| `tests/pulse/modules/test_game_module.py` | IntentSpec、patrol、store |
