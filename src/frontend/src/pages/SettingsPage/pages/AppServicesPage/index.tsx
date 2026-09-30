import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import Loading from "@/components/ui/loading";
import { api } from "@/controllers/API/api";
import { BASE_URL_API } from "@/customization/config-constants";
import useAlertStore from "@/stores/alertStore";

// App Services: the backends Agent Apps call (0to1-agents-market/docs/agent-apps.md).
type AppService = {
  id: string;
  name: string;
  mode: "url" | "image";
  url?: string | null;
  image?: string | null;
  imageDigest?: string | null;
  status: "verified" | "failed";
  verificationError?: string | null;
  verifiedAt?: string | null;
  manifest?: {
    version: string;
    intents: Record<string, { billing?: { max_credits: number } }>;
  } | null;
  secretHint: string;
  signingSecret?: string;
};

const URL_BASE = `${BASE_URL_API}app-services`;
const QUERY_KEY = ["useGetAppServices"];

function errorText(e: any): string {
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.problems)
    return `${detail.message}: ${detail.problems.map((p: any) => `${p.where} ${p.message}`).join("; ")}`;
  return detail?.message ?? e?.message ?? "Unknown error";
}

export default function AppServicesPage() {
  const queryClient = useQueryClient();
  const setErrorData = useAlertStore((s) => s.setErrorData);
  const setSuccessData = useAlertStore((s) => s.setSuccessData);
  const { data: services, isLoading } = useQuery<AppService[]>({
    queryKey: QUERY_KEY,
    queryFn: async () => (await api.get<AppService[]>(URL_BASE)).data,
  });
  const [addOpen, setAddOpen] = useState(false);
  const [mode, setMode] = useState<"image" | "url">("image");
  const [target, setTarget] = useState("");
  const [registryUser, setRegistryUser] = useState("");
  const [registryPassword, setRegistryPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY });

  const run = async (label: string, fn: () => Promise<any>) => {
    setBusy(true);
    try {
      const result = await fn();
      await refresh();
      return result;
    } catch (e: any) {
      setErrorData({ title: label, list: [errorText(e)] });
      return null;
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    const body =
      mode === "url"
        ? { mode, url: target.trim() }
        : {
            mode,
            image: target.trim(),
            ...(registryUser ? { registry_username: registryUser, registry_password: registryPassword } : {}),
          };
    const created = await run("Could not register the service", async () => (await api.post(URL_BASE, body)).data);
    if (created) {
      setAddOpen(false);
      setTarget("");
      setRegistryUser("");
      setRegistryPassword("");
      setSecret({ name: created.name, value: created.signingSecret });
    }
  };

  const verify = (s: AppService) =>
    run(`Could not refresh ${s.name}`, async () => {
      await api.post(`${URL_BASE}/${s.id}/verify`);
      setSuccessData({ title: `${s.name} refreshed` });
    });

  const rotate = async (s: AppService) => {
    const r = await run(`Could not rotate the secret of ${s.name}`, async () =>
      (await api.post(`${URL_BASE}/${s.id}/rotate-secret`)).data,
    );
    if (r) setSecret({ name: s.name, value: r.signingSecret });
  };

  const remove = (s: AppService) => run(`Could not delete ${s.name}`, () => api.delete(`${URL_BASE}/${s.id}`));

  return (
    <div className="flex h-full w-full flex-col gap-6">
      <div className="flex w-full items-start justify-between gap-6">
        <div className="flex flex-col">
          <h2 className="flex items-center text-lg font-semibold tracking-tight" data-testid="settings_menu_header">
            App Services
            <ForwardedIconComponent name="Server" className="ml-2 h-5 w-5 text-primary" />
          </h2>
          <p className="text-sm text-muted-foreground">
            Backends for Agent Apps, written with the agents-market SDK. Pick one in the Agent App Query node.
          </p>
        </div>
        <Button variant="primary" onClick={() => setAddOpen(true)} data-testid="add-app-service-button">
          <ForwardedIconComponent name="Plus" className="w-4" />
          <span>Add App Service</span>
        </Button>
      </div>

      <div className="flex h-full flex-col gap-1">
        {isLoading && <Loading />}
        {services && services.length === 0 && (
          <div className="w-full pt-8 text-center text-sm text-muted-foreground">No App Services yet</div>
        )}
        {services?.map((s) => {
          const intents = Object.entries(s.manifest?.intents ?? {});
          const paid = intents.filter(([, i]) => i.billing).length;
          return (
            <div
              key={s.id}
              className="flex items-center justify-between rounded-lg px-3 py-2 shadow-sm transition-colors hover:bg-accent"
              data-testid={`app-service-${s.name}`}
            >
              <div className="flex min-w-0 flex-col">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{s.name}</span>
                  {s.manifest && <span className="text-xs text-muted-foreground">v{s.manifest.version}</span>}
                  <Badge variant="secondary" className="px-1.5 py-0 text-xs">
                    {s.mode === "image" ? "Image" : "URL"}
                  </Badge>
                  <Badge variant={s.status === "verified" ? "successStatic" : "errorStatic"} className="px-1.5 py-0 text-xs">
                    {s.status === "verified" ? "Verified" : "Failed"}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {intents.length} intents{paid ? `, ${paid} paid` : ""}
                  </span>
                </div>
                <span className="truncate text-xs text-muted-foreground" title={s.imageDigest ?? s.url ?? ""}>
                  {s.mode === "image" ? `${s.image} → ${s.imageDigest?.slice(0, 19)}…` : s.url}
                  {"  ·  secret "}
                  {s.secretHint}
                </span>
                {s.verificationError && (
                  <span className="text-xs text-accent-red-foreground">{s.verificationError}</span>
                )}
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="iconSm" disabled={busy} className="text-muted-foreground">
                    <ForwardedIconComponent name="Ellipsis" className="h-5 w-5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => verify(s)}>
                    <ForwardedIconComponent name="RefreshCw" className="mr-2 h-4 w-4" />
                    Refresh (re-read manifest{s.mode === "image" ? ", re-resolve tag" : ""})
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => rotate(s)}>
                    <ForwardedIconComponent name="KeyRound" className="mr-2 h-4 w-4" />
                    Rotate signing secret
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => remove(s)} className="text-destructive">
                    <ForwardedIconComponent name="Trash2" className="mr-2 h-4 w-4" />
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          );
        })}
      </div>

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add App Service</DialogTitle>
            <DialogDescription>
              The platform reads the service's manifest to check it before saving.
            </DialogDescription>
          </DialogHeader>
          <div className="flex gap-2">
            {(["image", "url"] as const).map((m) => (
              <Button
                key={m}
                variant={mode === m ? "default" : "outline"}
                size="sm"
                onClick={() => setMode(m)}
                data-testid={`app-service-mode-${m}`}
                aria-pressed={mode === m}
              >
                {m === "image" ? "Container image" : "API endpoint"}
              </Button>
            ))}
          </div>
          <div className="flex flex-col gap-2">
            <Label>{mode === "image" ? "Image" : "Base URL"}</Label>
            <Input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder={mode === "image" ? "localhost:5000/stock-advisor-service:0.1.0" : "https://my-service.example.com"}
              data-testid="app-service-target"
            />
            {mode === "image" ? (
              <>
                <p className="text-xs text-muted-foreground">
                  The tag is pinned to its digest now; later pushes to the same tag change nothing until you Refresh.
                </p>
                <Label>Registry user (private images only)</Label>
                <Input value={registryUser} onChange={(e) => setRegistryUser(e.target.value)} />
                <Label>Registry token</Label>
                <Input type="password" value={registryPassword} onChange={(e) => setRegistryPassword(e.target.value)} />
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                Must be a public host. For a service on your laptop, expose it with a tunnel (cloudflared).
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={add} disabled={!target.trim() || busy} loading={busy}>
              Verify and save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!secret} onOpenChange={(o) => !o && setSecret(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Signing secret for {secret?.name}</DialogTitle>
            <DialogDescription>
              Shown once. URL-mode services set it as AGENTS_MARKET_SERVICE_SECRET; image-mode services get it automatically.
            </DialogDescription>
          </DialogHeader>
          <Input readOnly value={secret?.value ?? ""} onFocus={(e) => e.target.select()} />
          <DialogFooter>
            <Button
              variant="primary"
              onClick={() => {
                navigator.clipboard?.writeText(secret?.value ?? "");
                setSecret(null);
              }}
            >
              Copy and close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
