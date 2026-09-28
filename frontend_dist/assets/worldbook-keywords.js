export function splitWorldBookKeywords(value) {
  const seen = new Set();
  return String(value ?? '')
    .split(/[\s,，;；、|]+/u)
    .map(keyword => keyword.trim().toLocaleLowerCase())
    .filter(keyword => keyword && !seen.has(keyword) && seen.add(keyword));
}
