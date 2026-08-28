# 桥接适配层

Form C 的另一半：把具体生态的子 skill 与约定**映射**进通用路由表。

## 约束

- 只做声明映射，不改动 `core/` / `evolution/` / `deploy/` 任何逻辑。
- 不耦合核心；子 skill 自有 SEM 保留在子 skill 内部，与套件进化层互不越权。
- 每个生态一个目录 `bridge/<eco>/`。

## 约定布局

```
bridge/<eco>/
├── mapping.json   生态约定 → 路由表字段的映射（triggers / scope / auth / mode …）
└── handler.py     可选：生态专属 native 调用适配
```

## 状态

首个桥接方：leyao / Pms（待实现）。
