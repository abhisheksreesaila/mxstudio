# Developing MX Studio with Agent Skills

Agent skills are reusable instructions for a coding agent. They help contributors implement MX Studio consistently; they are not automatically features available to an MX Studio end user. Runtime product behavior still belongs in the Python domain modules, CLI, API, notebooks, and optional web UI.

## What This Repository Provides

- `.github/copilot-instructions.md` is always-on project guidance. It carries the product north star, architecture boundaries, and verification rules into coding sessions.
- `.github/skills/mxstudio-model-onboarding/SKILL.md` is the workflow for Hugging Face discovery, MAX compatibility, architecture ports, weights, and evidence gates.
- `.github/skills/mxstudio-inference-experience/SKILL.md` is the workflow for serving, GPU/compiler experiments, benchmarks, notebooks, visualizers, and Learn features.

VS Code discovers these files from the cloned repository. There is no package installation step for the project skills. Open the repository in VS Code, start Copilot Chat in Agent mode, and either describe a matching task or invoke the skill by typing `/` and selecting it. The skill description determines when Copilot loads it automatically.

## Where Skills Live

| Location | Purpose |
| --- | --- |
| `.github/skills/<name>/SKILL.md` | Shared, version-controlled project skill |
| `.agents/skills/<name>/SKILL.md` | Shared project skill compatible with multiple agents |
| `~/.copilot/skills/<name>/SKILL.md` | Personal Copilot skill used across repositories |
| `~/.agents/skills/<name>/SKILL.md` | Personal cross-agent skill |
| `.mxstudio/skills/modular/` | MX Studio's ignored runtime cache of Modular's model bring-up skills |

Keep MX Studio-specific development workflows in `.github/skills`. Put a skill in a home-directory location only when it should apply to unrelated projects too.

## Modular Model Bring-Up Skills

MX Studio's Architectures workflow already integrates Modular's `import-model` skill. When an Inspect or Implement architecture stage first needs it, `architectures.ensure_import_model_skill()` clones <https://github.com/modular/skills> into `.mxstudio/skills/modular`. That cache is intentionally ignored by Git.

To install the same official skills for direct use by coding agents across repositories:

```sh
git clone https://github.com/modular/skills.git ~/.cache/modular-skills
mkdir -p ~/.agents/skills
ln -s ~/.cache/modular-skills/import-model ~/.agents/skills/import-model
ln -s ~/.cache/modular-skills/debug-model ~/.agents/skills/debug-model
ln -s ~/.cache/modular-skills/eval-model ~/.agents/skills/eval-model
ln -s ~/.cache/modular-skills/profile-model ~/.agents/skills/profile-model
```

The symbolic links matter because MX Studio discovers individual skill folders directly under a skill root, at paths such as `~/.agents/skills/import-model/SKILL.md`. If those paths already exist, update the existing installation rather than replacing it blindly. For MX Studio runtime use, its automatic installer is simpler. Review third-party scripts before execution and pin a revision when reproducibility matters.

## Applying a Skill

Give the agent a concrete outcome and evidence target, for example:

```text
Use the MX Studio model onboarding skill to assess org/model, add truthful metadata if MAX supports it, and stop before download if this RTX 3090 cannot fit it.
```

```text
Use the MX Studio inference experience skill to add a CLI-first KV-cache lesson with an editable notebook, a measured result, and an optional GUI view.
```

The expected implementation loop is:

1. Start from one model or inference question.
2. Reuse the owning domain module.
3. Expose a simple CLI operation and matching API when useful.
4. Add an editable, sourced learning experience where useful.
5. Record evidence and label hardware-dependent claims.
6. Run focused tests, then the hermetic suite.

## Making the Vision Real

Build thin vertical experiences in this order:

1. **Model acquisition:** richer Hugging Face discovery, licenses, hardware fit, and a one-command supported-model path.
2. **Model adaptation:** complete the architecture evidence gates and integrate Modular's debug, evaluation, and profiling skills as managed jobs.
3. **Inference labs:** real MAX graph, compiler, memory, serving, and profiling experiments behind bounded execution APIs and editable notebooks.
4. **CLI parity:** ensure every core operation can be scripted without the browser.
5. **Hardware proof:** maintain tested capability reports for NVIDIA/Linux and Apple silicon rather than inferring support from platform names.
6. **Application profiles:** add legal and health workflows only with privacy, licensing, evaluation, oversight, and regulatory requirements designed in from the start.

The architectural rule is simple: skills encode how contributors work; MX Studio code delivers what users can run.