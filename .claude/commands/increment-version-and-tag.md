Bump the patch version in `jupyter_blender/blender_manifest.toml`, commit, tag, and push to GitHub.

## Steps

1. **Read the manifest** at `jupyter_blender/blender_manifest.toml` and find the `version` field.
2. **Increment the patch version** (e.g. `0.1.1` → `0.1.2`).
3. **Write the updated version** back to `blender_manifest.toml`.
4. **Commit** the change with message `Bump version to X.X.X`.
5. **Tag** the commit as `vX.X.X` and **push** both the commit and the tag to GitHub (`git push origin main --tags`).

## Notes

- Only touch the `version` field in `blender_manifest.toml` — nothing else.
- The repo must be clean (no uncommitted changes) before starting; warn the user if it isn't.
- Confirm the tag was pushed successfully before reporting done.
