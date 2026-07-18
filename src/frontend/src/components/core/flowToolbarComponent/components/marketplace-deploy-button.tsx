import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { useGetDeploymentStatus } from "@/controllers/API/queries/flows/use-get-deployment-status";
import {
  usePostDeployMarketplace,
  type A2AConfig,
} from "@/controllers/API/queries/flows/use-post-deploy-marketplace";
import useAlertStore from "@/stores/alertStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import A2aConfigModal from "@/modals/a2aConfigModal";

export default function MarketplaceDeployButton() {
  const currentFlow = useFlowsManagerStore((s) => s.currentFlow);
  const flowId = currentFlow?.id;
  const [showA2aModal, setShowA2aModal] = useState(false);
  const setSuccessData = useAlertStore((s) => s.setSuccessData);
  const setErrorData = useAlertStore((s) => s.setErrorData);
  const queryClient = useQueryClient();

  // Prior deployment (if any) — powers form prefill + version floor + status.
  const { data: deploymentStatus } = useGetDeploymentStatus({ flowId });

  const { mutate: deploy, isPending } = usePostDeployMarketplace({
    // Non-blocking: the executor now returns immediately with status
    // "starting". We just confirm the request landed, close the modal, and let
    // the header status indicator poll readiness in the background.
    onSuccess: () => {
      setShowA2aModal(false);
      setSuccessData({ title: "Deployment started" });
      if (flowId) {
        queryClient.invalidateQueries({
          queryKey: ["useGetDeploymentStatus", flowId],
        });
      }
    },
    onError: (err: any) => {
      const detail =
        err?.response?.data?.detail ?? err?.message ?? "Deploy failed";
      setErrorData({ title: "Publish failed", list: [String(detail)] });
    },
  });

  const handleClick = () => {
    if (!flowId || isPending) return;
    setShowA2aModal(true);
  };

  const handleA2aConfirm = (a2aConfig: A2AConfig) => {
    if (!flowId) return;
    deploy({ flowId, a2a_config: a2aConfig });
  };

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={isPending || !flowId}
        className="relative inline-flex h-8 items-center justify-start gap-1.5 rounded border border-primary bg-background px-2 text-sm font-normal text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
        data-testid="marketplace-deploy-btn"
        title="Publish to Marketplace"
      >
        <ForwardedIconComponent
          name="Store"
          className={`h-4 w-4 ${isPending ? "animate-pulse" : ""}`}
        />
        <span className="font-normal text-mmd">
          {isPending ? "Publishing..." : "Publish"}
        </span>
      </button>
      <A2aConfigModal
        open={showA2aModal}
        setOpen={setShowA2aModal}
        flowName={currentFlow?.name ?? ""}
        onConfirm={handleA2aConfirm}
        isPending={isPending}
        initialConfig={deploymentStatus?.a2a_config}
        deploymentStatus={deploymentStatus}
      />
    </>
  );
}
