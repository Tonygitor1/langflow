import { useState } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { usePostDeployMarketplace } from "@/controllers/API/queries/flows/use-post-deploy-marketplace";
import useFlowsManagerStore from "@/stores/flowsManagerStore";

export default function MarketplaceDeployButton() {
  const currentFlow = useFlowsManagerStore((s) => s.currentFlow);
  const [deployed, setDeployed] = useState(false);

  const { mutate: deploy, isPending } = usePostDeployMarketplace({
    onSuccess: () => {
      setDeployed(true);
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
    setDeployed(false);
    deploy({ flowId: currentFlow.id });
  };

  return (
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
  );
}
