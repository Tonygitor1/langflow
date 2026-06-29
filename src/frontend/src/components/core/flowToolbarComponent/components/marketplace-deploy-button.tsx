import { useState } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import {
  usePostDeployMarketplace,
  type A2AConfig,
} from "@/controllers/API/queries/flows/use-post-deploy-marketplace";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import A2aConfigModal from "@/modals/a2aConfigModal";

export default function MarketplaceDeployButton() {
  const currentFlow = useFlowsManagerStore((s) => s.currentFlow);
  const [deployed, setDeployed] = useState(false);
  const [showA2aModal, setShowA2aModal] = useState(false);

  const { mutate: deploy, isPending } = usePostDeployMarketplace({
    onSuccess: () => {
      setDeployed(true);
      setShowA2aModal(false);
      setTimeout(() => setDeployed(false), 3000);
    },
    onError: (err: any) => {
      const detail =
        err?.response?.data?.detail ?? err?.message ?? "Deploy failed";
      alert(`Deploy to Marketplace failed: ${detail}`);
    },
  });

  const handleClick = () => {
    if (!currentFlow?.id || isPending) return;
    setShowA2aModal(true);
  };

  const handleA2aConfirm = (a2aConfig: A2AConfig) => {
    if (!currentFlow?.id) return;
    setDeployed(false);
    deploy({ flowId: currentFlow.id, a2a_config: a2aConfig });
  };

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={isPending || !currentFlow?.id}
        className="relative inline-flex h-8 items-center justify-start gap-1.5 rounded border border-primary bg-background px-2 text-sm font-normal text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
        data-testid="marketplace-deploy-btn"
        title="Deploy to Marketplace"
      >
        <ForwardedIconComponent
          name={deployed ? "CircleCheck" : "Store"}
          className={`h-4 w-4 ${isPending ? "animate-pulse" : ""}`}
        />
        <span className="font-normal text-mmd">
          {isPending ? "Deploying..." : deployed ? "Deployed!" : "Publish"}
        </span>
      </button>
      <A2aConfigModal
        open={showA2aModal}
        setOpen={setShowA2aModal}
        flowName={currentFlow?.name ?? ""}
        onConfirm={handleA2aConfirm}
        isPending={isPending}
      />
    </>
  );
}
