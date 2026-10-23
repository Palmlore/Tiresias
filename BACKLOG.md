# Backlog

Known gaps that a change deliberately left for later. Each pull request is reviewed by an adversarial second model ([DuelLoop](docs/ai-usage.md#development-tools)); when a real finding is out of scope for that change, it is **deferred** here rather than dropped, in the same commit as the deferral.

Every item states all four: why it is out of scope for the change that found it, what bounds the harm meanwhile, where it was found (PR), and what would make it urgent. Items that survive to submission are summarised in [docs/limitations.md](docs/limitations.md).

Format:

```
## B-<n>: <title>
Found in: #<PR>  ·  Status: open | done (#<PR>)
- Out of scope because: …
- Safe to leave because: …
- Urgent if: …
- Evidence: <command, file:line, or trace>
```

---

_No items yet._
