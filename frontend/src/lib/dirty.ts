/**
 * JSON-compares two values to detect unsaved edits. Returns false when
 * either operand is null/undefined so the Apply button stays dot-less
 * until the page has loaded its initial config.
 *
 * Usage:
 *   const dirty = isDirty(form, loadedSnapshot)
 */
export function isDirty<T>(form: T | null | undefined, snapshot: T | null | undefined): boolean {
  if (form == null || snapshot == null) return false
  return JSON.stringify(form) !== JSON.stringify(snapshot)
}
