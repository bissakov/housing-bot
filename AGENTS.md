## Handoff

If the task created or modified files that Git tracks or would track, suggest
one commit message:

```
(<type>[+<type>...]): imperative lowercase description
```


Parentheses are mandatory; the description is imperative, lowercase, no
trailing period. No attribution of any kind: no `Co-Authored-By`, no
"Generated with" trailers, no tool signatures.

Use the most specific type: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`,
`style`, `build`, `ci`, `revert`, or `chore`. Prefer one type and never add
assistant/tool attribution or trailers.
