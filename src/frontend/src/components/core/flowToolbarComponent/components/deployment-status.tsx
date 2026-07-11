import type { ReactNode } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import ShadTooltip from "@/components/common/shadTooltipComponent";
import {
  type DeploymentStatusResponse,
  isDeploymentInProgress,
  useGetDeploymentStatus,
} from "@/controllers/API/queries/flows/use-get-deployment-status";
import { cn } from "@/utils/utils";

// Map a deployment status to an icon + color + label for the header indicator.
function statusVisual(status: string): {
  icon: string;
  spin?: boolean;
  className: string;
  label: string;
} {
  switch (status) {
    case "starting":
      return {
        icon: "Loader2",
        spin: true,
        className: "text-muted-foreground",
        label: "Deploying…",
      };
    case "ready":
      return { icon: "CircleCheck", className: "text-accent-emerald-foreground", label: "Ready" };
    case "failed":
      return { icon: "CircleX", className: "text-destructive", label: "Failed" };
    case "stopped":
      return { icon: "CircleMinus", className: "text-muted-foreground", label: "Stopped" };
    default:
      return { icon: "Info", className: "text-muted-foreground", label: status };
  }
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

/** Presentational status block — reused in the header tooltip and the modal. */
export function DeploymentStatusBlock({
  data,
}: {
  data: DeploymentStatusResponse;
}) {
  const { label } = statusVisual(data.status);
  const version = data.version ?? data.argocd?.version ?? data.a2a_config?.version;
  return (
    <div className="flex min-w-52 flex-col gap-1 text-xs">
      <Row label="Status" value={label} />
      {version && <Row label="Version" value={version} />}
      {data.argocd?.sync && <Row label="ArgoCD sync" value={data.argocd.sync} />}
      {data.argocd?.health && (
        <Row label="ArgoCD health" value={data.argocd.health} />
      )}
      {data.status === "failed" && data.last_error && (
        <div className="mt-1 max-w-72 break-words text-destructive">
          {data.last_error}
        </div>
      )}
    </div>
  );
}

/**
 * Header indicator: shows nothing until a flow has been published. While
 * deploying it spins; on hover it reveals the full status block. Polls in the
 * background (the query hook stops polling once the deploy settles).
 */
export function FlowDeploymentStatusIndicator({ flowId }: { flowId?: string }) {
  const { data } = useGetDeploymentStatus({ flowId });

  if (!data || data.status === "not_deployed" || data.status === "unknown") {
    return null;
  }

  // While still settling (starting, or ArgoCD Progressing) show a spinner,
  // regardless of the row's terminal status.
  const inProgress = isDeploymentInProgress(data);
  const visual = inProgress
    ? { icon: "Loader2", spin: true, className: "text-muted-foreground" }
    : statusVisual(data.status);

  return (
    <ShadTooltip
      content={<DeploymentStatusBlock data={data} />}
      side="bottom"
      align="end"
      avoidCollisions
    >
      <button
        type="button"
        aria-label="Deployment status"
        data-testid="deployment-status-indicator"
        className="flex h-8 w-8 items-center justify-center rounded hover:bg-muted"
      >
        <ForwardedIconComponent
          name={visual.icon}
          className={cn(
            "h-4 w-4",
            visual.className,
            visual.spin && "animate-spin",
          )}
        />
      </button>
    </ShadTooltip>
  );
}
