# Contributing to Zhulong

- Maintain the protocol in docs/zhulong/SOP.md and its references/presets; generate skills/zhulong with scripts/zhulong/sync_skill.py.
- Keep protocol requirements separate from what the structural checker can actually detect.
- Run python -m pytest tests -q and the skill synchronization check for relevant changes.
- Use synthetic fixtures in tests. Keep credentials, private research and raw third-party web captures out of this repository.
- Document regressions, scope limits and reproducible evidence; do not claim general accuracy from one example.
