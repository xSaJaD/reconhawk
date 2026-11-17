export function programType(value?: string): string {
  if (value === 'bbp' || value === 'bounty') return 'bounty';
  return value === 'vdp' ? 'vdp' : 'unknown';
}
export function syncIsActive(status?: string): boolean {
  return ['queued', 'running'].includes(status || '');
}
export function syncSummary(job: any): string {
  if (!job || job.status === 'idle') return '';
  if (job.importing) return 'Importing HackerOne programs…';
  return `${job.status}: ${job.completed || 0}/${job.total || 0} scopes · ${job.succeeded || 0} synced · ${job.failed || 0} failed${job.error ? ` · ${job.error}` : ''}`;
}
