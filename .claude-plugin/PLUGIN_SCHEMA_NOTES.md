# Plugin Manifest Schema Notes

This document captures undocumented but enforced constraints of the Claude Code plugin manifest validator.

## Required Fields

### `version` (MANDATORY)
Required by the validator. Example: `"version": "1.0.0"`

## Field Shape Rules

The following fields **must always be arrays**:
- `commands`
- `skills`
- `hooks` (if present)

Even with one entry, strings are not accepted.

## The `agents` Field: DO NOT ADD
Do NOT add an `"agents"` field to `plugin.json`. Agent `.md` files under `agents/` are discovered automatically by convention. Any form of `agents` field causes validation error.

## The `hooks` Field: DO NOT ADD
Claude Code auto-loads `hooks/hooks.json` from any installed plugin by convention. Declaring `hooks` in `plugin.json` causes "Duplicate hooks file detected" error.

## The `mcpServers` Field: Keep the Empty Opt-Out
Keep `"mcpServers": {}` in `plugin.json` to prevent plugin installs from auto-loading root MCP definitions.

## Path Resolution
- `commands` and `skills` accept directory paths only when wrapped in arrays
- Explicit file paths are safest

## Known-Good Minimal Example
```json
{
  "version": "1.0.0",
  "commands": ["./commands/"],
  "skills": ["./skills/"]
}
```

**No `hooks` field. No `agents` field. Both auto-loaded.**
