---
title: Agent Eval Harness
hide:
  - navigation
  - toc
---

<div class="aeh-proposal-banner" markdown>

**Docs UX preview** for [opendatahub-io/agent-eval-harness](https://github.com/opendatahub-io/agent-eval-harness) —
hosted on a personal fork to show owners a clearer landing experience.
Not a long-lived product fork; install and clone URLs still point upstream.

</div>

<p class="aeh-brand-logo" markdown>
![Red Hat](assets/images/redhat-logo.png)
</p>

# Make agent performance measurable — and improvable

Evaluate Claude Code skills and agent capabilities with one declarative
`eval.yaml`: analyze, generate cases, run, judge, trace in MLflow, then
optimize. Same config on your laptop, Harbor containers, or EvalHub.

<p class="aeh-badges" markdown>
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-informational)
![Claude plugin](https://img.shields.io/badge/claude-plugin-7c5cff)
![MLflow](https://img.shields.io/badge/mlflow-traces-orange)
![Harbor](https://img.shields.io/badge/harbor-containers-success)
</p>

[Get started :material-arrow-right:](get-started/index.md){ .md-button .md-button--primary }
[eval.yaml reference :material-arrow-right:](reference/eval-yaml.md){ .md-button }

![Agent Eval Harness loop: setup, analyze, dataset, run, mlflow, optimize](assets/images/aeh-loop.svg){ .aeh-hero-visual }

---

## How the loop works

Five stages. Only analyze → dataset → run are required for a first score.

<div class="grid cards" markdown>

-   **1 · Analyze**

    ---

    Point `/eval-analyze` at a skill or a prompt brief. The harness writes
    `eval.yaml` with judges, schema, and thresholds.

-   **2 · Dataset**

    ---

    `/eval-dataset` fills cases from your schema — or bring your own
    `cases/` tree with gold references.

-   **3 · Run & judge**

    ---

    `/eval-run` executes on Claude Code (or another runner), scores with
    LLM + code judges, and emits a rich HTML report.

-   **4 · Trace**

    ---

    Optional `/eval-mlflow` syncs metrics, artifacts, and hierarchical
    GenAI traces for every case.

-   **5 · Optimize**

    ---

    `/eval-optimize` proposes skill fixes from failures and re-runs so
    you keep only real gains.

</div>

[See the full pipeline guide :material-arrow-right:](guides/pipeline.md)

---

## What you get

<div class="grid cards" markdown>

-   :material-file-cog: **Skill or prompt mode**

    ---

    Test a packaged skill (`execution.skill`) or agent capability directly
    (`execution.prompt`) — including agentic documentation checks.

    [:octicons-arrow-right-24: Execution model](concepts/execution-model.md)

-   :material-gavel: **LLM + code judges**

    ---

    Built-in judges, inline Python checks, rubrics, pairwise A/B, and
    N-sample stability — all in one config.

    [:octicons-arrow-right-24: Judges & scoring](concepts/judges.md)

-   :material-server-network: **One config, three backends**

    ---

    Local subprocess, Harbor (Podman / OpenShift), or EvalHub — backend is
    a CLI flag, never baked into `eval.yaml`.

    [:octicons-arrow-right-24: Execution backends](concepts/backends.md)

-   :material-robot-happy: **Any agent runtime**

    ---

    Claude Code out of the box; bring OpenCode or a custom CLI / Responses
    API runner when you need it.

    [:octicons-arrow-right-24: Runners](concepts/runners.md)

-   :material-trophy: **Reward API for RL**

    ---

    Collapse judges into a `[0, 1]` reward for GRPO-style training via
    Harbor / NeMo Gym / SkyRL.

    [:octicons-arrow-right-24: Reward API](concepts/reward-api.md)

-   :material-chart-timeline: **MLflow-native**

    ---

    Experiments, datasets, hierarchical traces, and feedback sync — opt in
    with one `mlflow:` block.

    [:octicons-arrow-right-24: Tracing](concepts/tracing.md)

</div>

---

## Choose your path

| Path | Use it when | Start |
|---|---|---|
| **Claude Code plugin** | You want slash commands in an existing project | `claude plugin install agent-eval-harness@opendatahub-skills` |
| **Local clone** | You are hacking on the harness itself | `git clone https://github.com/opendatahub-io/agent-eval-harness` |
| **Harbor / OpenShift** | You need containerized, reproducible trials | [Running on Harbor](guides/harbor.md) |

```bash
# Upstream install (proposal preview does not change this)
claude plugin install agent-eval-harness@opendatahub-skills
/eval-setup
/eval-analyze --skill my-skill
/eval-dataset
/eval-run --model opus
```

---

## Explore the docs

<div class="grid cards" markdown>

-   :material-school: **Get Started**

    ---

    Install and run your first evaluation end to end.

    [:octicons-arrow-right-24: Get Started](get-started/index.md)

-   :material-book-open-variant: **Guides**

    ---

    Task-oriented how-tos for every skill and backend.

    [:octicons-arrow-right-24: Guides](guides/index.md)

-   :material-lightbulb-on: **Concepts**

    ---

    Execution model, judges, rewards, and tracing.

    [:octicons-arrow-right-24: Concepts](concepts/index.md)

-   :material-chef-hat: **Cookbook**

    ---

    Worked configs for common evaluation scenarios.

    [:octicons-arrow-right-24: Cookbook](cookbook/index.md)

</div>
