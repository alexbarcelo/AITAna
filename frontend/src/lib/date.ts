/**
 * Formats an ISO timestamp as an unambiguous yyyy-mm-dd date, using the
 * browser's local timezone (Date's getFullYear/getMonth/getDate are already
 * local-time -- only the UTC variants or toISOString() would give UTC).
 */
export function formatDate(iso: string): string {
  const d = new Date(iso)
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

/**
 * Same as `formatDate`, plus a local hh:mm -- used wherever "when was this
 * created" needs to disambiguate same-day entries (e.g. several batches
 * uploaded on the same date).
 */
export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${formatDate(iso)} ${hh}:${min}`
}
