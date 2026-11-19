import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, request } from "../api";
import { exact, when } from "../format";
import { Button, Card, ErrorNotice, PageTitle, StatTile, WarnNote } from "../components/ui";

// Owner admin page (#/admin): Munito AI spend and status. The token is ADMIN_TOKEN from Render,
// kept in this browser only; the API answers 404 when ADMIN_TOKEN is not set.

interface Row { feature: string; model?: string; calls: number; usd: number; input_tokens?: number; output_tokens?: number }
interface MunitoAdmin {
  key_set: boolean;
  status: "active" | "paused" | "stopped" | "off";
  status_reason: string;
  features: { feature: string; env: string; on: boolean }[];
  total_spent_usd: number;
  credit_usd: number;
  credit_left_usd: number;
  today_usd: number;
  daily_cap_usd: number;
  total_cap_usd: number;
  usage_tier: string | null;
  tier: string;
  last_error: string | null;
  last_error_at: string | null;
  stopped_reason: string | null;
  by_feature_today: Row[];
  by_feature_model_total: Row[];
  prices_checked: string;
  note: string;
}

const TOKEN = "shiftlens.admin_token";
const read = () => { try { return localStorage.getItem(TOKEN) || ""; } catch { return ""; } };
const save = (t: string) => { try { localStorage.setItem(TOKEN, t); } catch { /* private mode */ } };
const forget = () => { try { localStorage.removeItem(TOKEN); } catch { /* ignore */ } };

const usd = (x: number) => (x === 0 ? "$0" : x < 0.01 ? `$${x.toFixed(6)}` : `$${x.toFixed(2)}`);
const usd4 = (x: number) => `$${x.toFixed(4)}`;     // credit left: small spends stay visible
const FEATURE = { coverage: "Full coverage checks", topics: "Topic names", weekly: "Weekly report",
  scorecard: "Munito scorecard", smoke: "Step 0 test calls" } as Record<string, string>;

function TokenForm({ onToken }: { onToken: (t: string) => void }) {
  const [t, setT] = useState("");
  const submit = (e: FormEvent) => { e.preventDefault(); if (t.trim()) { save(t.trim()); onToken(t.trim()); } };
  return (
    <Card>
      <form onSubmit={submit} className="flex flex-col sm:flex-row gap-3 sm:items-end">
        <label className="flex-1 min-w-0">
          <span className="block text-sm text-muted mb-1.5">Admin token (ADMIN_TOKEN in Render). Kept in this browser only.</span>
          <input value={t} onChange={e => setT(e.target.value)} type="password" required autoComplete="off" spellCheck={false}
                 className="w-full h-11 px-4 rounded-[10px] bg-raised border border-control text-text font-mono text-sm" />
        </label>
        <Button variant="primary" type="submit" disabled={!t.trim()}>Open</Button>
      </form>
    </Card>
  );
}

export default function Admin() {
  const [token, setToken] = useState(read);
  const [data, setData] = useState<MunitoAdmin | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (method: "GET" | "POST" = "GET") => {
    if (!token) return;
    setBusy(true); setErr(null);
    try {
      setData(await request<MunitoAdmin>(method === "GET" ? "/admin/munito" : "/admin/munito/clear-stop",
        { method, admin: token, body: method === "POST" ? {} : undefined }));
    } catch (e) {
      const x = e instanceof ApiError ? e : new ApiError(0, String(e));
      if (x.status === 401) { forget(); setToken(""); }
      setErr(x);
    } finally { setBusy(false); }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  const errText = err?.status === 401 ? "That admin token was not accepted."
    : err?.status === 404 ? "The admin page is off: ADMIN_TOKEN is not set in Render." : null;

  return (
    <>
      <PageTitle sub="Owner only. Munito AI spend and status.">Admin</PageTitle>
      {!token && <TokenForm onToken={setToken} />}
      {err && <div className="mt-4">{errText ? <WarnNote>{errText}</WarnNote> : <ErrorNotice error={err} onRetry={() => void load()} />}</div>}
      {token && !data && busy && <p className="text-muted" role="status">Loading…</p>}
      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <StatTile label="Total spent" value={usd(data.total_spent_usd)}
                      context={`cap ${usd(data.total_cap_usd)} (MUNITO_TOTAL_USD_CAP)`} />
            <StatTile label="Credit left" value={usd4(data.credit_left_usd)}
                      context={`${usd(data.credit_usd)} credit minus ShiftLens spend`}
                      tone={data.credit_left_usd < data.credit_usd - data.total_cap_usd + 5 ? "warn" : undefined} />
            <StatTile label="Today (UTC day)" value={usd(data.today_usd)}
                      context={`budget ${usd(data.daily_cap_usd)} (MUNITO_DAILY_USD), resets 05:30 IST`}
                      tone={data.today_usd >= data.daily_cap_usd ? "warn" : undefined} />
          </div>

          <Card title="Status" action={<Button variant="ghost" disabled={busy} onClick={() => void load()}>Refresh</Button>}>
            <p className="m-0 text-sm">
              <span className="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-[.1em]">
                <i className={`size-1.5 rounded-full ${data.status === "active" ? "bg-ok" : data.status === "off" ? "bg-subtle" : "bg-warn"}`} aria-hidden />
                {data.status === "active" && !data.features.some(f => f.on) ? "ready, all features off" : data.status}
              </span>
              {data.status === "off" && <span className="text-muted"> · MUNITO_API_KEY is not set, so every Munito feature is skipped.</span>}
              {data.status_reason && <span className="text-muted"> · {data.status_reason}</span>}
            </p>
            {data.stopped_reason && (
              <div className="mt-4 space-y-3">
                <WarnNote>Munito features are off: {data.stopped_reason}</WarnNote>
                <Button variant="secondary" disabled={busy} onClick={() => void load("POST")}>Turn Munito back on</Button>
                <p className="text-xs text-subtle m-0">This clears the error stop only. The total cap still applies to every call.</p>
              </div>
            )}
            <ul className="mt-4 mb-0 pl-0 list-none grid gap-2 sm:grid-cols-3">
              {data.features.map(f => (
                <li key={f.feature} className="text-sm">
                  <span className={f.on ? "text-ok" : "text-muted"}>{f.on ? "on" : "off"}</span>{" "}
                  {FEATURE[f.feature] ?? f.feature} <span className="font-mono text-xs text-subtle">{f.env}</span>
                </li>
              ))}
            </ul>
            <p className="text-xs text-subtle mt-4 mb-0">
              Service tier {data.tier}{data.usage_tier ? ` · Munito usage tier: ${data.usage_tier}` : ""} · prices checked {data.prices_checked}
            </p>
            {data.last_error && (
              <p className="text-xs text-subtle mt-2 mb-0">Last error <span title={exact(data.last_error_at)}>{when(data.last_error_at)}</span>: {data.last_error}</p>
            )}
          </Card>

          <Card title="Spend by feature">
            {data.by_feature_model_total.length === 0 ? <p className="m-0 text-sm text-muted">No Munito calls yet.</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-muted">
                    <th className="font-normal py-2 pr-4">Feature and model</th>
                    <th className="font-normal py-2 pr-4 text-right">Calls</th><th className="font-normal py-2 pr-4 text-right hidden sm:table-cell">Tokens in / out</th>
                    <th className="font-normal py-2 text-right">Spent</th></tr></thead>
                  <tbody>
                    {data.by_feature_model_total.map(r => (
                      <tr key={`${r.feature}-${r.model}`} className="border-t border-line">
                        <td className="py-2 pr-4">{FEATURE[r.feature] ?? r.feature}
                          <span className="block font-mono text-xs text-subtle break-all">{r.model}</span></td>
                        <td className="py-2 pr-4 text-right num">{r.calls}</td>
                        <td className="py-2 pr-4 text-right num hidden sm:table-cell">{r.input_tokens} / {r.output_tokens}</td>
                        <td className="py-2 text-right num">{usd(r.usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-xs text-subtle mt-4 mb-0">{data.note}</p>
          </Card>

          <Button variant="ghost" onClick={() => { forget(); setToken(""); setData(null); }}>Forget the token in this browser</Button>
        </div>
      )}
    </>
  );
}
