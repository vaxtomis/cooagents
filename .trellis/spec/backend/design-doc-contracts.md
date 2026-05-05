# DesignDoc Contracts

> Executable contract for the Markdown DesignDoc produced by `DesignWork` and revalidated by `DevWork`.

---

## Scenario: DesignDoc structure contract tightening

### 1. Scope / Trigger

- Trigger: cross-layer contract change
- Why code-spec depth is required:
  - `DesignWork` prompt generation changed
  - `DesignWork` validator behavior changed
  - `DevWork` Step1 consumes the same DesignDoc shape
  - fixture/test artifacts had to move in lockstep

### 2. Signatures

- `src.design_prompt_composer.compose_prompt(inputs: PromptInputs) -> str`
- `src.design_validator.validate_design_markdown(text: str, *, required_sections: list[str], mockup_sections: list[str]) -> ValidationReport`
- `src.config.DesignConfig.required_sections: list[str]`
- `src.config.DesignConfig.mockup_sections: list[str]`

### 3. Contracts

#### Front-matter contract

Required keys:

- `title`
- `goal`
- `version`
- `rubric_threshold`
- `needs_frontend_mockup`

#### Required H2 sections

The backend contract is:

1. `## 用户故事`
2. `## 场景案例`
3. `## 详细操作流程`
4. `## 验收标准`
5. `## 打分 rubric`

If `needs_frontend_mockup: true`, also require:

- `## 页面结构`
- a body line containing `设计图链接或路径:`

#### `场景案例` contract

Medium-strength validation:

- At least one scenario case is required
- Each case must start with `### SC-xx <title>`
- Each case must include:
  - `Actor`
  - `Main Flow`
  - `Expected Result`
- `Trigger` and `Preconditions` are prompt-guided recommended fields, not hard validator requirements

Allowed actors are broader than end users, for example:

- `User`
- `Admin`
- `Operator`
- `System`
- `Scheduler`
- `Worker`

#### `验收标准` contract

Medium-strength validation:

- Use checklist items
- Each item must use `AC-xx` numbering
- Expected shape: `- [ ] AC-xx: ...`
- Items must stay testable and observable
- The validator does not enforce one fixed sentence grammar

#### `打分 rubric` contract

Medium-strength validation:

- Must be a Markdown table
- Required columns:
  - `维度`
  - `权重`
  - `判定标准`
- Each `权重` cell must be integer-like
- Total weight summing to 100 is recommended by prompt, not enforced by validator

### 4. Validation & Error Matrix

| Condition | Expected outcome |
|---|---|
| document lacks opening front-matter block | validation error mentioning front-matter |
| required front-matter key missing | `missing_fm_keys` includes the key |
| required H2 section missing | `missing_sections` includes the section |
| scenario section has no `### SC-xx` case | validator error for missing scenario case |
| scenario case missing `Actor` / `Main Flow` / `Expected Result` | validator error naming the missing field |
| acceptance section lacks checklist `AC-xx` items | validator error for acceptance format |
| rubric section is not a markdown table | validator error for rubric table |
| rubric table lacks required columns | validator error naming missing columns |
| rubric weight cell is non-integer | validator error for invalid weight |
| mockup requested but `页面结构` or `设计图链接或路径` missing | missing section and/or mockup-specific error |

### 5. Good / Base / Bad Cases

- Good:
  - `场景案例` uses `### SC-01 登录成功` and includes `Actor`, `Main Flow`, `Expected Result`
  - `验收标准` uses `- [ ] AC-01: ...`
  - `打分 rubric` is a markdown table with `维度 | 权重 | 判定标准`

- Base:
  - Scenario cases omit `Trigger` / `Preconditions` but still pass
  - Rubric weights do not sum to 100 but rows are integer-like, so validation still passes

- Bad:
  - `用户案例` remains as a free-form bullet list with no `SC-xx`
  - Acceptance criteria are plain bullets without checklist or `AC-xx`
  - Rubric is prose or a non-table block

### 6. Tests Required

- Unit:
  - `tests/test_design_validator.py`
  - assertion points:
    - scenario heading rule
    - scenario required fields
    - acceptance checklist rule
    - rubric table / columns / integer-weight rules

- Unit:
  - `tests/test_design_prompt_composer.py`
  - assertion points:
    - prompt mentions `场景案例`
    - prompt explains `SC-xx`, `AC-xx`, and rubric table columns

- Workflow regression:
  - `tests/test_design_work_sm.py`
  - `tests/test_design_works_route.py`
  - assertion points:
    - valid fixtures still complete
    - incomplete fixtures still loop / escalate for the right reason

- Downstream consumer regression:
  - `tests/test_dev_work_sm.py`
  - `tests/test_dev_works_route.py`
  - `tests/test_dev_work_step5_multi_repo.py`
  - assertion points:
    - DevWork Step1 still accepts a valid DesignDoc under the tightened contract

### 7. Wrong vs Correct

#### Wrong

```md
## 用户案例

- 新用户首次登录
- 老用户切换设备登录

## 验收标准

- 登录成功
- 登录失败有提示

## 打分 rubric

完整性 20 分，对齐度 30 分
```

#### Correct

```md
## 场景案例

### SC-01 首次登录

- Actor: User
- Main Flow:
  1. 用户输入账号密码
  2. 系统校验并创建会话
- Expected Result: 用户成功进入首页

## 验收标准

- [ ] AC-01: 当账号密码正确时，应成功进入首页

## 打分 rubric

| 维度 | 权重 | 判定标准 |
|---|---:|---|
| 完整性 | 20 | 章节和字段齐全 |
```
