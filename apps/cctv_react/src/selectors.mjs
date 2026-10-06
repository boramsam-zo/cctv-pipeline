export function filterCases(cases, search, model, state) {
  const query = search.trim().toLocaleLowerCase();
  return cases.filter(item => (model === '전체' || item.model === model)
    && (state === '전체' || item.state === state)
    && [item.id, item.title, item.location, item.kind].join(' ').toLocaleLowerCase().includes(query));
}
export function pageCases(items, page, size) {
  const count = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(Math.max(1, page), count);
  return {items: items.slice((current - 1) * size, current * size), current, count};
}
