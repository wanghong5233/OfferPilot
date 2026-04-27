# Driver Types

`GameDriver` 是 Game module 与执行环境之间的唯一边界。

## 1. 返回契约

| 字段 | 说明 |
|---|---|
| `ok` | 是否成功 |
| `source` | driver 名 |
| `status` | 可选状态,如 `sent` / `not_implemented` |
| `error` | 失败机器码 |
| `error_message` | 面向调试的短信息 |

禁止返回 `ok=true` 但没有执行真实动作。

## 2. `adb_airtest`

| 能力 | 当前状态 |
|---|---|
| ADB health | 已实现 |
| 包名探测 | 已实现 |
| 前台 app 检测 | 已实现 |
| 截图 | 已实现 |
| tap / swipe / text | 已实现 |
| 模板匹配 | Airtest 未安装时返回 `not_implemented` |
| OCR | PR3 |

## 3. `cloud_web`

`cloud_web` 是抽象验证 stub。它证明 pipeline 不依赖 ADB,但不实接 Patchright。

## 4. 新增 driver

新增 driver 只需要:

1. 继承 `GameDriver`
2. 保持字典返回契约
3. 在 `_connectors/registry.py` 注册
4. 新增单元测试覆盖 `health`、`screenshot`、`tap`、`find_template`
