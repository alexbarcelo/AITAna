/** Record ids are integers; URLs, search params and `<select>` values carry
 * them as strings. `undefined` for anything that isn't a positive integer. */
export function parseId(value: string | null | undefined): number | undefined {
  if (!value) return undefined
  const id = Number(value)
  return Number.isInteger(id) && id > 0 ? id : undefined
}
