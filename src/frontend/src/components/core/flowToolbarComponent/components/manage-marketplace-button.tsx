import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { useGetDeploymentStatus } from "@/controllers/API/queries/flows/use-get-deployment-status";
import useFlowsManagerStore from "@/stores/flowsManagerStore";

// Where the marketplace UI is served. Overridable per environment.
const MARKETPLACE_UI_URL =
  import.meta.env.VITE_MARKETPLACE_UI_URL ?? "http://localhost:3001";

/**
 * "Manage Agents in Marketplace" — opens the marketplace manage page for this
 * flow's deployed agent (agent_id == flow_id). Enabled only once a deployment
 * record exists (status is anything other than not_deployed/unknown).
 */
export default function ManageMarketplaceButton() {
  const currentFlow = useFlowsManagerStore((s) => s.currentFlow);
  const flowId = currentFlow?.id;

  const { data: deploymentStatus } = useGetDeploymentStatus({ flowId });
  const status = deploymentStatus?.status;
  const isDeployed =
    Boolean(flowId) &&
    status !== undefined &&
    status !== "not_deployed" &&
    status !== "unknown";

  const handleClick = () => {
    if (!isDeployed || !flowId) return;
    window.open(
      `${MARKETPLACE_UI_URL}/agents/${flowId}/manage`,
      "_blank",
      "noopener,noreferrer",
    );
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={!isDeployed}
      className="relative inline-flex h-8 items-center justify-start gap-1.5 rounded border border-primary bg-background px-2 text-sm font-normal text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
      data-testid="manage-marketplace-btn"
      title={
        isDeployed
          ? "Manage Agents in Marketplace"
          : "Publish this flow first to manage it in the marketplace"
      }
    >
      <ForwardedIconComponent name="Store" className="h-4 w-4" />
      <span className="font-normal text-mmd">Manage</span>
    </button>
  );
}
