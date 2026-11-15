"use client";
export function ScopeStatus({ program, onRetry }: any) {
  if (program.platform !== 'hackerone') return null;
  const sync = program.scope_sync || {};
  const status = sync.status || 'never';
  const active = ['queued', 'syncing'].includes(status);
  return <div className="scope-status" onClick={event => event.stopPropagation()}>
    <span className={'pill ' + (['error', 'rate_limited', 'interrupted'].includes(status) ? 'sev-high' : '')}>Scope: {status}</span>
    {!active && onRetry && <button type="button" aria-label={`Retry scope for ${program.name}`} onClick={() => onRetry(program.id)}>Retry scope</button>}
    {sync.error && <div role="alert" className="sev-high">{sync.error}</div>}
    {status === 'empty' && <div className="muted">The API returned no supported eligible web assets.</div>}
    {sync.synced_at && <div className="muted">Last success: {new Date(sync.synced_at).toLocaleString()}</div>}
    {(sync.warnings || []).map((warning: string, index: number) => <div className="muted" key={index}>{warning}</div>)}
  </div>;
}
