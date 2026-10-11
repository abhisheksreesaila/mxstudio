// mxconsole, the top-bar hardware chip's numbers. Pure.
const GB = 1024;
const round1 = (v) => Math.round(v * 10) / 10;

/** gpus: /admin/metrics `gpus`. crew: /admin/system `crew` (each running model has gpu_mb when the daemon can tell).
 *  Segments are the VRAM each running model holds, then what is left over ("other"), or one "in use" segment
 *  when the daemon can't attribute memory to models. */
export function hardwareView(gpus, crew) {
  const g = (gpus || [])[0];
  if (!g) return { present: false, label: "no GPU", name: "", short: "", usedGb: 0, totalGb: 0, freeGb: 0, segments: [] };
  const total = g.total_mb / GB, used = g.used_mb / GB;
  const perModel = (crew || []).filter((c) => /^model:/.test(c.id || "") && c.gpu_mb > 0)
    .map((c) => ({ name: c.id.slice(6), gb: round1(c.gpu_mb / GB) }));
  const segments = [...perModel];
  const attributed = perModel.reduce((a, s) => a + s.gb, 0);
  if (perModel.length && used - attributed > 0.05) segments.push({ name: "other", gb: round1(used - attributed) });
  if (!perModel.length && used > 0) segments.push({ name: "in use", gb: round1(used) });
  for (const s of segments) s.pct = total ? (s.gb / total) * 100 : 0;
  return {
    present: true, name: g.name, short: g.name.replace(/^NVIDIA\s+/i, "").replace(/^GeForce\s+/i, ""),
    usedGb: round1(used), totalGb: round1(total), freeGb: round1(total - used), segments,
    label: `${round1(used)}/${round1(total)} GB`,
  };
}
