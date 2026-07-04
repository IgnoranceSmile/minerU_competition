# Contracts

接口定义与类型契约。

## 内容

- `interfaces.py` — 核心数据结构与接口定义（Drawing, Pipeline, TaskResult 等）

## 规则

1. **改契约必须同步所有引用**：修改 interfaces.py 后必须更新所有使用方
2. **版本化**：破坏性变更需谨慎，确保下游调用方同步更新
